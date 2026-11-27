import json
import os
import signal
import stat
import tempfile
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from openapi_schema_validator import OAS31Validator, oas31_format_checker

from nurse import __main__ as program
from nurse import records
from nurse.reception import (
    HermesCheck,
    Reception,
    limit_reached,
    parse_prescription,
    refuse_unchecked,
)
from nurse.records import RX_ID, Launch, State, StateError, load_state
from nurse.settings import Settings, load_catalogue, load_settings
from stetho.yamlfile import load_yaml

ROOT = Path(__file__).parents[2]
CONTRACT: Any = load_yaml(ROOT / "stetho" / "openapi.yaml")
ACTIONS = load_catalogue(ROOT / "config" / "actions.example.toml")
NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
EPOCH = int(NOW.timestamp())


def answer(status: object, **changes: object) -> dict[str, object]:
    document: dict[str, object] = {
        "rx_id": "rx-1",
        "status": status,
        "refusal_reason": None,
        "exit_code": 0,
        "output_excerpt": None,
        "anomaly": None,
        "started_at": "2026-01-15T11:59:00Z",
        "finished_at": "2026-01-15T11:59:30Z",
        "verified": True,
    }
    return {**document, **changes}


def settings(tmp_path: Path) -> Settings:
    for name in ("prescriptions", "results", "log"):
        (tmp_path / name).mkdir(exist_ok=True)
    return replace(
        load_settings(ROOT / "config" / "nurse.example.toml"),
        prescriptions_dir=tmp_path / "prescriptions",
        results_dir=tmp_path / "results",
        log_dir=tmp_path / "log",
    )


def request(rx_id: str = "rx-1", **changes: object) -> dict[str, object]:
    document: dict[str, object] = {
        "rx_id": rx_id,
        "action": "RERUN_JOB",
        "target": "111111111111",
        "origin": "manual",
        "requested_by": "shared",
        "created_at": "2026-01-15T11:59:30Z",
    }
    return {**document, **changes}


def drop(tmp_path: Path, content: object, name: str = "rx-1.json") -> Path:
    path = tmp_path / "prescriptions" / name
    data = content if isinstance(content, bytes) else json.dumps(content).encode()
    path.write_bytes(data)
    return path


def reception(tmp_path: Path, check: HermesCheck = refuse_unchecked, **kwargs: Any) -> Reception:
    configured = settings(tmp_path)
    records.create_started(configured.log_dir, State())  # as when NURSE starts
    return Reception(configured, ACTIONS, kwargs.get("clock", lambda: NOW), check_hermes=check)


def allow(*_: object) -> str | None:
    return None


def result(tmp_path: Path, rx_id: str = "rx-1") -> dict[str, Any]:
    document = json.loads((tmp_path / "results" / f"{rx_id}.json").read_bytes())
    assert isinstance(document, dict)
    return document


def log(tmp_path: Path) -> list[dict[str, Any]]:
    document = json.loads((tmp_path / "results" / "_log.json").read_bytes())
    assert isinstance(document, list)
    return document


def journal(tmp_path: Path) -> list[dict[str, Any]]:
    text = (tmp_path / "log" / "journal.jsonl").read_text()
    return [json.loads(line) for line in text.splitlines()]


def check_schema(name: str, value: object) -> None:
    schema = {"$ref": f"#/components/schemas/{name}", "components": CONTRACT["components"]}
    OAS31Validator(schema, format_checker=oas31_format_checker).validate(value)


def reason_of(tmp_path: Path, content: object, check: HermesCheck = allow) -> str:
    service = reception(tmp_path, check)
    drop(tmp_path, content)
    assert service.run_once() == 1
    return str(result(tmp_path)["refusal_reason"])


def test_valid_request_is_refused_while_nothing_is_run(tmp_path: Path) -> None:
    service = reception(tmp_path, allow)
    path = drop(tmp_path, request())

    service.run_once()

    assert result(tmp_path) == {
        "rx_id": "rx-1",
        "status": "refused",
        "refusal_reason": "execution_disabled",
        "exit_code": None,
        "output_excerpt": None,
        "anomaly": None,
        "started_at": None,
        "finished_at": "2026-01-15T12:00:00Z",
        "verified": None,
    }
    check_schema("PrescriptionResult", result(tmp_path))
    check_schema("ActionLogEntry", log(tmp_path)[0])
    assert journal(tmp_path) == log(tmp_path)
    assert not path.exists()
    state = load_state(tmp_path / "log")
    assert (list(state.handled), state.launches) == (["rx-1"], [])


def test_until_hermes_is_read_every_action_is_refused(tmp_path: Path) -> None:
    assert reason_of(tmp_path, request(), refuse_unchecked) == "state_unreadable"


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"{",
        b"[]",
        b"\xff",
        b"[" * 4000,
        b"x" * 5000,
        json.dumps(request()).encode() + b" " * 4096,
        json.dumps(request())[:-1].encode() + b', "action": "PAUSE_JOB"}',
        json.dumps(request(target=float("nan"))).encode(),
        {k: v for k, v in request().items() if k != "origin"},
        request(extra=1),
        request(rx_id="other"),
        request(action="rerun"),
        request(action="RERUN_JOB\n"),
        request(action=3),
        request(target="abc; rm -rf /"),
        request(target="--help"),
        request(target=""),
        request(target=["a"]),
        request(origin="admin"),
        request(requested_by=""),
        request(requested_by="a\nb"),
        request(requested_by="a" * 65),
        request(requested_by="\ud800"),
        request(requested_by=None),
        request(created_at="2026-01-15"),
        request(created_at="2026-01-15T11:59:30"),
        request(created_at="2026-02-30T11:59:30Z"),
        request(created_at="2026-01-15T11:59:30+99:00"),
        request(created_at=EPOCH),
        request(created_at="2026-01-15T11:59:30+00:99"),
        request(created_at="2026-01-15T11:59:30-00:60"),
    ],
)
def test_invalid_request_is_refused_and_logged_without_its_content(
    tmp_path: Path, content: object
) -> None:
    assert reason_of(tmp_path, content) == "invalid_request"
    assert log(tmp_path)[0]["prescription"] is None
    check_schema("ActionLogEntry", log(tmp_path)[0])
    assert list((tmp_path / "prescriptions").iterdir()) == []


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"created_at": "2026-01-15T11:54:59Z"}, "expired"),
        ({"created_at": "2026-01-15T11:55:00Z"}, "execution_disabled"),
        ({"created_at": "2026-01-15T12:01:00Z"}, "execution_disabled"),
        ({"created_at": "2026-01-15T12:01:01Z"}, "expired"),
        ({"created_at": "2026-01-15T13:00:00+01:00"}, "execution_disabled"),
        ({"created_at": "0001-01-01T00:00:00+23:59"}, "expired"),
        ({"created_at": "9999-12-31T23:59:59-23:59"}, "expired"),
        ({"action": "DELETE_ALL"}, "unknown_action"),
        ({"origin": "auto"}, "auto_disabled"),
        ({"action": "RESTART_GATEWAY", "target": None, "origin": "auto"}, "auto_disabled"),
        ({"action": "RESTART_GATEWAY"}, "invalid_target"),
        ({"target": None}, "invalid_target"),
        ({"target": "ABCDEF123456"}, "invalid_target"),
        ({"action": "RESTART_GATEWAY", "target": None}, "execution_disabled"),
    ],
)
def test_each_check_gives_its_reason(
    tmp_path: Path, changes: dict[str, object], expected: str
) -> None:
    assert reason_of(tmp_path, {**request(), **changes}) == expected


def test_hermes_is_asked_only_about_requests_that_passed_the_other_checks(
    tmp_path: Path,
) -> None:
    asked: list[str | None] = []

    def check(_action: object, target: str | None, _now: datetime) -> str | None:
        asked.append(target)
        return "target_not_found"

    service = reception(tmp_path, check)
    drop(tmp_path, request(action="DELETE_ALL"))
    drop(tmp_path, request("rx-2", target="bad"), "rx-2.json")
    drop(tmp_path, request("rx-3"), "rx-3.json")

    service.run_once()

    assert asked == ["111111111111"]
    assert result(tmp_path, "rx-3")["refusal_reason"] == "target_not_found"


@pytest.mark.parametrize("kind", ["link", "hard link", "pipe"])
def test_only_a_plain_file_without_link_is_read(tmp_path: Path, kind: str) -> None:
    service = reception(tmp_path, allow)
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text(json.dumps(request()))
    path = tmp_path / "prescriptions" / "rx-1.json"
    if kind == "link":
        path.symlink_to(elsewhere)
    elif kind == "hard link":
        path.hardlink_to(elsewhere)
    else:
        os.mkfifo(path)

    service.run_once()

    assert result(tmp_path)["refusal_reason"] == "invalid_request"
    assert not path.is_symlink()
    assert not path.exists()
    assert elsewhere.exists()


@pytest.mark.parametrize(
    "name", [".rx-1.json", "-x.json", "_log.json", "a b.json", "rx-1.txt", "a" * 65 + ".json"]
)
def test_other_names_are_left_alone(tmp_path: Path, name: str) -> None:
    service = reception(tmp_path, allow)
    path = drop(tmp_path, request(), name)

    assert service.run_once() == 0
    assert path.exists()
    assert list((tmp_path / "results").iterdir()) == []


def test_folders_do_not_take_the_place_of_requests(tmp_path: Path) -> None:
    service = reception(tmp_path, allow)
    for number in range(30):
        (tmp_path / "prescriptions" / f"a{number:02}.json").mkdir()
    drop(tmp_path, request("z"), "z.json")

    assert service.run_once() == 1


def test_few_requests_are_handled_at_each_pass(tmp_path: Path) -> None:
    service = reception(tmp_path, allow)
    for number in range(25):
        drop(tmp_path, b"", f"r{number:02}.json")

    assert [service.run_once() for _ in range(3)] == [20, 5, 0]


def test_a_replay_is_refused_and_keeps_the_first_answer(tmp_path: Path) -> None:
    service = reception(tmp_path, allow)
    drop(tmp_path, request())
    service.run_once()
    first = result(tmp_path)
    drop(tmp_path, request(action="PAUSE_JOB"))

    # A new reception, as after a restart.
    reception(tmp_path, allow).run_once()

    assert result(tmp_path) == first
    assert log(tmp_path)[0]["result"]["refusal_reason"] == "duplicate"


def test_a_replay_is_known_even_when_its_result_is_gone(tmp_path: Path) -> None:
    service = reception(tmp_path, allow)
    drop(tmp_path, request())
    service.run_once()
    (tmp_path / "results" / "rx-1.json").unlink()
    drop(tmp_path, request())

    service.run_once()

    assert result(tmp_path)["refusal_reason"] == "duplicate"


def with_state(**changes: object) -> bytes:
    """A state that is right but for the given part."""
    document: dict[str, object] = {
        "handled": {},
        "launches": [],
        "recent": [],
        "actions": [],
        "pending": {},
    }
    return json.dumps({**document, **changes}).encode()


@pytest.mark.parametrize(
    "content",
    [
        b"{",
        b"[]",
        b"{}",
        with_state(handled={"a": "x"}),
        with_state(launches=[{}]),
        with_state(launches={}),
        with_state(recent=[1]),
        with_state(actions=[{"prescription": None, "result": 1}]),
        with_state(actions=[{"prescription": None, "result": {}}]),
        with_state(actions=[{"prescription": None, "result": answer("done", rx_id=[])}]),
        with_state(recent=[{"prescription": 1, "result": answer("refused")}]),
        with_state(recent=[{"prescription": None, "result": answer("refused", rx_id="../x")}]),
        with_state(pending={"a": {"prescription": 1, "started_at": "x"}}),
        with_state(pending={"a": {"prescription": {"rx_id": "b"}, "started_at": "x"}}),
        with_state(
            pending={"a": {"prescription": {"rx_id": "a", "n": float("nan")}, "started_at": "x"}}
        ),
        with_state(pending=[]),
        with_state(pending={"../x": {"prescription": {}, "started_at": "x"}}),
        with_state(pending={"a": {"prescription": {}}}),
        with_state(launches=[{"at": True, "action": "A", "origin": "manual", "restart": False}]),
        b" " * (4 * 1024 * 1024 + 1),
    ],
)
def test_an_unusable_state_refuses_everything_and_is_kept(tmp_path: Path, content: bytes) -> None:
    service = reception(tmp_path, allow)
    service.recover()
    (tmp_path / "results" / "_log.json").unlink()
    (tmp_path / "log" / "state.json").write_bytes(content)
    drop(tmp_path, request())

    service.run_once()

    assert result(tmp_path)["refusal_reason"] == "state_unreadable"
    assert (tmp_path / "log" / "state.json").read_bytes() == content
    assert not (tmp_path / "results" / "_log.json").exists()


def test_missing_private_folder_is_not_a_first_start(tmp_path: Path) -> None:
    with pytest.raises(StateError):
        load_state(tmp_path / "absent")


def test_nothing_is_answered_when_the_state_cannot_be_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def full(*_: object) -> None:
        raise OSError("made-up private path")

    monkeypatch.setattr("nurse.reception.save_state", full)
    service = reception(tmp_path, allow)
    path = drop(tmp_path, request())

    assert [service.run_once(), service.run_once()] == [1, 0]
    assert path.exists()
    assert list((tmp_path / "results").iterdir()) == []
    assert "made-up" not in caplog.text


def test_what_stetho_reads_can_be_read_by_its_group_only(tmp_path: Path) -> None:
    reason_of(tmp_path, request())

    modes = {
        path.name: stat.S_IMODE(path.stat().st_mode)
        for folder in ("results", "log")
        for path in (tmp_path / folder).iterdir()
    }

    assert modes == {
        "rx-1.json": 0o640,
        "_log.json": 0o640,
        "state.json": 0o600,
        "started.json": 0o600,
        "journal.jsonl": 0o600,
    }


def test_a_request_named_like_the_log_cannot_replace_it(tmp_path: Path) -> None:
    assert not RX_ID.fullmatch(records.LOG_NAME.removesuffix(".json"))
    service = reception(tmp_path, allow)
    drop(tmp_path, request("log"), "log.json")

    service.run_once()

    assert result(tmp_path, "log")["rx_id"] == "log"
    assert len(log(tmp_path)) == 1


def entry(rx_id: str, finished: str, status: str = "refused") -> dict[str, Any]:
    return {
        "prescription": {"rx_id": rx_id},
        "result": {"rx_id": rx_id, "status": status, "finished_at": finished},
    }


def test_only_the_last_refusals_are_kept_apart_from_the_actions() -> None:
    state = State(actions=[entry("real", "2026-01-15T10:00:00Z", "done")])
    for number in range(200):
        state.remember(entry(str(number), "2026-01-15T11:00:00Z"))

    assert len(state.recent) == 20
    assert len(state.log()) == 21
    assert state.log()[-1]["result"]["rx_id"] == "real"


def test_old_identifiers_and_launches_are_forgotten() -> None:
    state = State(
        {"old": EPOCH - 600, "kept": EPOCH - 599},
        [Launch(EPOCH - age, "A", "manual", False) for age in (3600, 3599, -50)],
    )

    state.forget(EPOCH)

    assert list(state.handled) == ["kept"]
    assert [EPOCH - item.at for item in state.launches] == [3599, -50]


def launched(code: str, ago: int, origin: str = "manual", restart: bool = False) -> Launch:
    return Launch(EPOCH - ago, code, origin, restart)


@pytest.mark.parametrize(
    ("code", "origin", "launches", "maximum", "expected"),
    [
        ("PAUSE_JOB", "manual", [launched("PAUSE_JOB", 9)] * 4, 7, None),
        ("PAUSE_JOB", "manual", [launched("PAUSE_JOB", 9)] * 5, 7, "rate_limited"),
        ("PAUSE_JOB", "manual", [launched("PAUSE_JOB", 9, "auto")] * 5, 7, None),
        ("RESTART_GATEWAY", "auto", [launched("RESTART_GATEWAY", 200, "auto")] * 2, 7,
         "rate_limited"),
        ("PAUSE_JOB", "manual", [launched("OTHER", 9)] * 7, 7, "rate_limited"),
        ("PAUSE_JOB", "manual", [launched("OTHER", 9)] * 6, 7, None),
        ("RESTART_CONTAINER", "manual", [launched("RESTART_GATEWAY", 119, "auto", True)], 7,
         "too_soon"),
        ("RESTART_GATEWAY", "manual", [launched("RESTART_CONTAINER", 120, restart=True)], 7,
         None),
        ("PAUSE_JOB", "manual", [launched("RESTART_GATEWAY", 1, restart=True)], 7, None),
    ],
)  # fmt: skip
def test_limits(
    code: str, origin: str, launches: list[Launch], maximum: int, expected: str | None
) -> None:
    state = State(launches=launches)

    assert limit_reached(state, ACTIONS[code], origin, maximum, EPOCH) == expected


def test_limits_apply_to_requests_after_a_restart(tmp_path: Path) -> None:
    service = reception(tmp_path, allow)
    state = State(launches=[launched("RERUN_JOB", 9)] * 5)
    records.save_state(tmp_path / "log", state)
    drop(tmp_path, request())

    service.run_once()

    assert result(tmp_path)["refusal_reason"] == "rate_limited"
    assert len(load_state(tmp_path / "log").launches) == 5


def test_old_and_excess_results_are_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(records, "MAX_RESULTS", 2)
    ages = {"a": 8 * 86400, "b": 30, "c": 20, "d": 10, "_log": 9 * 86400}
    for name, age in ages.items():
        path = tmp_path / f"{name}.json"
        path.write_text("{}")
        os.utime(path, (EPOCH - age, EPOCH - age))

    records.prune_results(tmp_path, EPOCH)

    assert sorted(p.name for p in tmp_path.iterdir()) == ["_log.json", "c.json", "d.json"]


def test_journal_keeps_a_few_files_of_limited_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(records, "JOURNAL_BYTES", 60)
    for number in range(20):
        records.append_journal(tmp_path, {"prescription": None, "result": {"n": number}})

    files = sorted(path.name for path in tmp_path.iterdir())

    assert files == ["journal.jsonl", *(f"journal.jsonl.{n}" for n in range(1, 5))]
    assert '"n": 19' in (tmp_path / "journal.jsonl").read_text()
    assert '"n": 18' in (tmp_path / "journal.jsonl.1").read_text()


def test_request_larger_than_the_limit_is_not_parsed() -> None:
    content = json.dumps(request()).encode()

    assert parse_prescription(content + b" " * (4096 - len(content)), "rx-1") is not None
    assert parse_prescription(content + b" " * (4097 - len(content)), "rx-1") is None


def test_formats_are_those_of_the_contract() -> None:
    schemas = CONTRACT["components"]["schemas"]

    assert schemas["PrescriptionId"]["pattern"] == f"^{RX_ID.pattern}$"
    assert set(schemas["Prescription"]["required"]) == set(request())
    assert parse_prescription(json.dumps(request()).encode(), "rx-1") is not None
    check_schema("Prescription", request())


def test_program_refuses_to_start_on_bad_settings(tmp_path: Path) -> None:
    assert program.main(["--config", str(tmp_path / "absent.toml")]) == 2


def test_program_handles_requests_until_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    stop = threading.Event()
    calls: list[int] = []

    def fail_then_stop(_self: Reception) -> int:
        calls.append(1)
        if len(calls) == 3:
            stop.set()
        raise RuntimeError("made-up private text")

    monkeypatch.setattr(Reception, "run_once", fail_then_stop)
    monkeypatch.setattr(program, "_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(program, "load_settings", lambda _: settings(tmp_path))
    monkeypatch.setattr(program, "load_catalogue", lambda _: ACTIONS)
    monkeypatch.setattr("signal.signal", lambda *_: None)

    assert program.main(["--config", tempfile.gettempdir()], stop) == 0
    assert len(calls) == 3
    assert caplog.text.count("cannot be handled: RuntimeError") == 1
    assert "made-up" not in caplog.text


def test_file_that_cannot_be_read_is_not_a_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_: object) -> bytes:
        raise OSError("made-up")

    service = reception(tmp_path, allow)
    drop(tmp_path, request())
    monkeypatch.setattr("nurse.reception.os.read", broken)

    service.run_once()
    monkeypatch.undo()

    assert result(tmp_path)["refusal_reason"] == "invalid_request"


def test_state_that_cannot_be_opened_is_unusable(tmp_path: Path) -> None:
    (tmp_path / "state.json").mkdir()

    with pytest.raises(StateError):
        load_state(tmp_path)


def test_request_stays_when_the_journal_cannot_be_written(tmp_path: Path) -> None:
    service = reception(tmp_path, allow)
    (tmp_path / "log" / "journal.jsonl").mkdir()
    path = drop(tmp_path, request())

    service.run_once()

    assert path.exists()
    assert list((tmp_path / "results").iterdir()) == []


def test_result_removed_while_pruning_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "gone.json").write_text("{}")

    def vanished(_: Path) -> os.stat_result:
        raise FileNotFoundError

    monkeypatch.setattr(Path, "lstat", vanished)

    records.prune_results(tmp_path, EPOCH)
    monkeypatch.undo()

    assert [path.name for path in tmp_path.iterdir()] == ["gone.json"]


@pytest.mark.parametrize("number", [signal.SIGINT, signal.SIGTERM])
def test_program_stops_on_a_signal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, number: signal.Signals
) -> None:
    handlers: dict[int, Any] = {}
    stop = threading.Event()
    monkeypatch.setattr("signal.signal", handlers.__setitem__)
    monkeypatch.setattr(program, "load_settings", lambda _: settings(tmp_path))
    monkeypatch.setattr(program, "load_catalogue", lambda _: ACTIONS)
    monkeypatch.setattr(program, "watch", lambda _reception, _stop: handlers[number](number, None))

    assert program.main(["--config", "unused"], stop) == 0
    assert stop.is_set()


def old_result(tmp_path: Path, name: str) -> Path:
    path = tmp_path / "results" / name
    path.write_text("{}")
    os.utime(path, (EPOCH - 8 * 86400, EPOCH - 8 * 86400))
    return path


def test_old_results_are_removed_without_any_request(tmp_path: Path) -> None:
    moment = [NOW]
    service = reception(tmp_path, allow, clock=lambda: moment[0])
    first = old_result(tmp_path, "a.json")
    service.run_once()
    assert not first.exists()

    second = old_result(tmp_path, "b.json")
    service.run_once()
    assert second.exists()  # not at every pass
    moment[0] = NOW + timedelta(minutes=1)
    service.run_once()

    assert not second.exists()


@pytest.mark.parametrize("step", ["append_journal", "write_result", "write_log", "os.unlink"])
def test_request_that_cannot_be_finished_is_not_tried_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    def broken(*_: object, **__: object) -> None:
        raise PermissionError

    service = reception(tmp_path, allow)
    path = drop(tmp_path, request())
    monkeypatch.setattr(f"nurse.reception.{step}", broken)

    passes = [service.run_once(), service.run_once()]
    monkeypatch.undo()

    assert passes == [1, 0]
    assert path.exists()


def test_failed_save_leaves_no_trace_for_the_next_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved = records.save_state
    calls: list[int] = []

    def fail_once(log_dir: Path, state: State) -> None:
        calls.append(1)
        if len(calls) == 1:
            raise OSError
        saved(log_dir, state)

    monkeypatch.setattr("nurse.reception.save_state", fail_once)
    service = reception(tmp_path, allow)
    drop(tmp_path, request("a"), "a.json")
    drop(tmp_path, request("b"), "b.json")

    service.run_once()

    state = load_state(tmp_path / "log")
    assert list(state.handled) == ["b"]
    assert [entry["result"]["rx_id"] for entry in state.recent] == ["b"]


def test_too_many_stuck_files_stop_the_handling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(*_: object) -> None:
        raise OSError

    monkeypatch.setattr("nurse.reception.save_state", broken)
    monkeypatch.setattr("nurse.reception.MAX_STUCK", 20)
    service = reception(tmp_path, allow)
    for number in range(50):
        drop(tmp_path, b"", f"r{number:02}.json")

    assert [service.run_once(), service.run_once(), service.run_once()] == [20, 0, 0]
    assert caplog.text.count("nothing more is tried") == 1
    for number in range(20):
        (tmp_path / "prescriptions" / f"r{number:02}.json").unlink()
    assert service.run_once() == 20


def test_full_state_refuses_and_stays_loadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("nurse.reception.MAX_HANDLED", 2)
    service = reception(tmp_path, allow)
    for name in ("a", "b", "c"):
        drop(tmp_path, request(name), f"{name}.json")

    service.run_once()

    assert result(tmp_path, "c")["refusal_reason"] == "state_unreadable"
    assert list(load_state(tmp_path / "log").handled) == ["a", "b"]


def test_largest_state_can_be_loaded_again(tmp_path: Path) -> None:
    longest, wide = "a" * 64, "\U0001f600"
    prescription = {**request(longest), "requested_by": wide * 64, "target": "a" * 64}
    result = answer("unverified", rx_id=longest, output_excerpt=wide * 2048)
    ended = {"prescription": prescription, "result": result}
    refusal = {"prescription": prescription, "result": answer("refused", rx_id=longest)}
    state = State(
        {f"{number:064}": EPOCH for number in range(records.MAX_HANDLED)},
        [Launch(EPOCH, "A" * 64, "manual", False)] * 100,
        [refusal] * records.LOG_REFUSALS,
        [ended] * records.LOG_ACTIONS,
        {longest: {"prescription": prescription, "started_at": STARTED}},
    )
    records.save_state(tmp_path, state)
    (tmp_path / "started.json").write_text("{}")

    assert load_state(tmp_path) == state


def test_unusable_state_is_reported_once(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    service = reception(tmp_path, allow)
    (tmp_path / "log" / "state.json").write_bytes(b"{")
    for name in ("a", "b"):
        drop(tmp_path, request(name), f"{name}.json")
        service.run_once()

    assert caplog.text.count("cannot be read") == 1


def test_only_one_nurse_may_use_a_private_folder(tmp_path: Path) -> None:
    first = records.lock_instance(tmp_path)
    with pytest.raises(BlockingIOError):
        records.lock_instance(tmp_path)
    os.close(first)

    os.close(records.lock_instance(tmp_path))


def test_program_does_not_start_twice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configured = settings(tmp_path)
    monkeypatch.setattr(program, "load_settings", lambda _: configured)
    monkeypatch.setattr(program, "load_catalogue", lambda _: ACTIONS)
    held = records.lock_instance(configured.log_dir)

    try:
        assert program.main(["--config", "unused"]) == 2
    finally:
        os.close(held)


def test_half_written_files_are_removed(tmp_path: Path) -> None:
    for name in (".tmp-abc", "kept.json"):
        (tmp_path / name).write_text("x")

    records.remove_temporary_files(tmp_path)

    assert [path.name for path in tmp_path.iterdir()] == ["kept.json"]


STARTED = "2026-01-15T11:59:00Z"
LAUNCH = Launch(EPOCH, "RERUN_JOB", "manual", False)


def interrupted(tmp_path: Path) -> State:
    """An action that NURSE had started when it stopped."""
    reception(tmp_path)
    state = State()
    records.start_action(tmp_path / "log", state, request(), LAUNCH, STARTED)
    return state


def test_a_started_action_is_remembered_before_it_runs(tmp_path: Path) -> None:
    interrupted(tmp_path)

    state = load_state(tmp_path / "log")

    assert list(state.pending) == list(state.handled) == ["rx-1"]
    assert state.launches == [Launch(EPOCH, "RERUN_JOB", "manual", False)]


def test_nothing_changes_when_a_start_cannot_be_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_: object) -> None:
        raise PermissionError

    monkeypatch.setattr(records, "save_state", broken)
    state = State()

    with pytest.raises(PermissionError):
        records.start_action(tmp_path, state, request(), LAUNCH, STARTED)

    assert state == State()


def test_interrupted_action_is_unverified_and_never_run_again(tmp_path: Path) -> None:
    interrupted(tmp_path)
    service = reception(tmp_path, allow)
    path = drop(tmp_path, request())  # the file was still there

    service.recover()

    first = result(tmp_path)
    assert (first["status"], first["verified"], first["anomaly"]) == (
        "unverified",
        False,
        "action_unverified",
    )
    assert first["started_at"] == STARTED
    check_schema("PrescriptionResult", first)
    check_schema("ActionLogEntry", log(tmp_path)[0])
    service.run_once()
    assert not path.exists()
    assert result(tmp_path) == first
    state = load_state(tmp_path / "log")
    assert (state.pending, len(state.actions), len(state.launches)) == ({}, 1, 1)
    assert len((tmp_path / "log" / "actions.jsonl").read_text().splitlines()) == 1


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        (answer("done"), "done"),
        (answer("refused"), "unverified"),
        (answer("queued"), "unverified"),
        (answer("done", rx_id="other"), "unverified"),
        (answer(["done"]), "unverified"),
        (answer("done", finished_at=None), "unverified"),
        ({"rx_id": "rx-1", "status": "done"}, "unverified"),
        ({"rx_id": "rx-1"}, "unverified"),
        ([], "unverified"),
    ],
)
def test_answer_written_before_the_interruption_is_kept(
    tmp_path: Path, written: object, expected: str
) -> None:
    interrupted(tmp_path)
    (tmp_path / "results" / "rx-1.json").write_text(json.dumps(written))

    reception(tmp_path).recover()

    assert result(tmp_path)["status"] == expected


def test_a_pending_action_gets_no_other_answer(tmp_path: Path) -> None:
    interrupted(tmp_path)
    drop(tmp_path, request())

    reception(tmp_path, allow).run_once()

    assert not (tmp_path / "results" / "rx-1.json").exists()


@pytest.mark.parametrize("name", ["state.json", "started.json"])
@pytest.mark.parametrize(
    "content",
    [b"{", b"null", b"[]", b'{"../x": 1}', b'{"a": "x"}', b'{"a": true}', b"\xef\xbb\xbfnull"],
)
def test_program_does_not_start_with_an_unusable_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    name: str,
    content: bytes,
) -> None:
    configured = settings(tmp_path)
    (configured.log_dir / name).write_bytes(content)
    monkeypatch.setattr(program, "load_settings", lambda _: configured)
    monkeypatch.setattr(program, "load_catalogue", lambda _: ACTIONS)
    monkeypatch.setattr("signal.signal", lambda *_: None)

    stop = threading.Event()
    stop.set()

    assert program.main(["--config", "unused"], stop) == 2

    assert "its state cannot be read" in caplog.text
    assert (configured.log_dir / name).read_bytes() == content
    assert list(configured.results_dir.iterdir()) == []


def test_losing_the_identifiers_of_known_actions_is_not_a_first_start(tmp_path: Path) -> None:
    interrupted(tmp_path)
    (tmp_path / "log" / "started.json").unlink()

    with pytest.raises(StateError):
        load_state(tmp_path / "log")


def started_and_done(tmp_path: Path, state: State, rx_id: str, at: int) -> None:
    launch = Launch(at, "RERUN_JOB", "manual", False)
    records.start_action(tmp_path / "log", state, request(rx_id), launch, STARTED)
    done = answer("done", rx_id=rx_id)
    records.finish_action(tmp_path / "log", tmp_path / "results", state, done, at)


def test_action_older_than_the_last_80_is_still_known(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(records, "MAX_RESULTS", 0)
    monkeypatch.setattr(records, "LOG_ACTIONS", 2)
    service = reception(tmp_path, allow)
    state = State()
    for number in range(5):
        started_and_done(tmp_path, state, f"a{number}", EPOCH - 7200)
    first = result(tmp_path, "a0")
    drop(tmp_path, request("a0"), "a0.json")

    service.run_once()

    assert result(tmp_path, "a0") == first  # neither replaced nor removed
    assert log(tmp_path)[0]["result"]["refusal_reason"] == "duplicate"


def test_identifiers_of_actions_are_forgotten_after_8_days(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(records, "LOG_ACTIONS", 0)  # none stays listed in the state
    settings(tmp_path)
    state = State()
    started_and_done(tmp_path, state, "old", EPOCH - 8 * 86400)
    started_and_done(tmp_path, state, "kept", EPOCH - 8 * 86400 + 1)

    started_and_done(tmp_path, state, "new", EPOCH)

    assert sorted(load_state(tmp_path / "log").started) == ["kept", "new"]


def test_most_actions_that_8_days_can_hold_can_be_loaded_again(tmp_path: Path) -> None:
    started = {f"{number:064}": EPOCH for number in range(100 * 24 * 8)}
    (tmp_path / "started.json").write_text(json.dumps(started))

    assert len(load_state(tmp_path).started) == 19200


def test_program_does_not_start_when_recovery_cannot_be_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(_: Reception) -> None:
        raise OSError

    configured = settings(tmp_path)
    monkeypatch.setattr(program, "load_settings", lambda _: configured)
    monkeypatch.setattr(program, "load_catalogue", lambda _: ACTIONS)
    monkeypatch.setattr(Reception, "recover", broken)
    monkeypatch.setattr("signal.signal", lambda *_: None)

    assert program.main(["--config", "unused"]) == 2
    assert "OSError while preparing its state" in caplog.text
    os.close(records.lock_instance(configured.log_dir))


def test_a_flood_of_refusals_cannot_erase_an_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(records, "MAX_RESULTS", 5)
    monkeypatch.setattr(records, "JOURNAL_BYTES", 2000)
    interrupted(tmp_path)
    service = reception(tmp_path, allow)
    service.recover()
    for number in range(60):
        drop(tmp_path, request(f"f{number:02}", action="DELETE_ALL"), f"f{number:02}.json")

    while service.run_once():
        pass

    assert result(tmp_path)["status"] == "unverified"
    assert len(list((tmp_path / "results").iterdir())) == 5 + 2  # the action and the log
    assert [e["result"]["rx_id"] for e in log(tmp_path)].count("rx-1") == 1
    assert '"rx-1"' in (tmp_path / "log" / "actions.jsonl").read_text()


def test_folder_is_synced_once_the_new_file_is_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    synced: list[tuple[Path, bool]] = []
    monkeypatch.setattr(
        records, "_sync_folder", lambda f: synced.append((f, (f / "state.json").exists()))
    )

    records.save_state(tmp_path, State())

    assert synced == [(tmp_path, True)]


@pytest.mark.parametrize("held", [False, True])
def test_program_cleans_its_folders_only_with_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, held: bool
) -> None:
    configured = settings(tmp_path)
    left = [folder / ".tmp-x" for folder in (configured.log_dir, configured.results_dir)]
    for path in left:
        path.write_text("x")
    monkeypatch.setattr(program, "load_settings", lambda _: configured)
    monkeypatch.setattr(program, "load_catalogue", lambda _: ACTIONS)
    monkeypatch.setattr("signal.signal", lambda *_: None)
    stop = threading.Event()
    stop.set()
    lock = records.lock_instance(configured.log_dir) if held else None

    try:
        assert program.main(["--config", "unused"], stop) == (2 if held else 0)
    finally:
        if lock is not None:
            os.close(lock)

    assert [path.exists() for path in left] == [held, held]


def test_program_does_not_start_when_it_cannot_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    configured = settings(tmp_path)
    (configured.results_dir / ".tmp-folder").mkdir()
    monkeypatch.setattr(program, "load_settings", lambda _: configured)
    monkeypatch.setattr(program, "load_catalogue", lambda _: ACTIONS)
    monkeypatch.setattr("signal.signal", lambda *_: None)

    assert program.main(["--config", "unused"]) == 2
    assert "IsADirectoryError" in caplog.text
    assert str(tmp_path) not in caplog.text


def test_without_a_state_old_refusals_still_go_and_nothing_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(records, "MAX_RESULTS", 5)
    service = reception(tmp_path, allow)
    (tmp_path / "log" / "started.json").write_text("{}")
    (tmp_path / "log" / "state.json").write_bytes(b"{")
    kept = tmp_path / "results" / "action.json"
    kept.write_text(json.dumps(answer("done", rx_id="action")))
    (tmp_path / "results" / "damaged.json").write_text("{")
    old = old_result(tmp_path, "old.json")
    old.write_text(json.dumps(answer("refused", rx_id="old")))
    os.utime(old, (EPOCH - 8 * 86400,) * 2)
    for number in range(40):
        drop(tmp_path, request(f"f{number:02}"), f"f{number:02}.json")

    while service.run_once():
        pass

    names = {path.name for path in (tmp_path / "results").iterdir()}
    assert {"action.json", "damaged.json"} < names
    assert "old.json" not in names
    assert len(names) == 5 + 2


@pytest.mark.parametrize("step", ["write_result", "append_journal", "save_state", "write_log"])
def test_action_survives_a_failure_at_each_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    interrupted(tmp_path)

    def broken(*_: object, **__: object) -> None:
        raise PermissionError

    monkeypatch.setattr(records, step, broken)
    for _ in range(3):  # every restart fails the same way
        # Once the action is saved, only the public log is left: that does not stop NURSE.
        if step == "write_log":
            reception(tmp_path).recover()
        else:
            with pytest.raises(PermissionError):
                reception(tmp_path).recover()
    monkeypatch.undo()

    reception(tmp_path).recover()

    state = load_state(tmp_path / "log")
    assert (state.pending, len(state.actions)) == ({}, 1)
    assert result(tmp_path) == state.actions[0]["result"]
    assert log(tmp_path) == state.actions
    lines = (tmp_path / "log" / "actions.jsonl").read_text().splitlines()
    assert [json.loads(line) for line in lines] == state.actions


def synced_files(monkeypatch: pytest.MonkeyPatch, order: list[str]) -> None:
    """Records the name of every file or folder that is synced, and each saved state."""
    fsync, save = os.fsync, records.save_state

    def synced(descriptor: int) -> None:
        order.append(Path(f"/proc/self/fd/{descriptor}").readlink().name)
        fsync(descriptor)

    def saved(log_dir: Path, state: State) -> None:
        order.append("save state")
        save(log_dir, state)

    monkeypatch.setattr(os, "fsync", synced)
    monkeypatch.setattr(records, "save_state", saved)


@pytest.mark.parametrize("written_before", [False, True])
def test_actions_journal_is_synced_before_the_action_stops_being_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, written_before: bool
) -> None:
    interrupted(tmp_path)
    if written_before:  # the line is there, but its sync had failed
        kept = answer("done")
        (tmp_path / "results" / "rx-1.json").write_text(json.dumps(kept))
        line = {"prescription": request(), "result": kept}
        records.append_journal(tmp_path / "log", line, records.ACTIONS_NAME)
    order: list[str] = []
    synced_files(monkeypatch, order)

    reception(tmp_path).recover()

    before_state = order[: order.index("save state")]
    journal_at = before_state.index("actions.jsonl")
    assert before_state[journal_at + 1] == "log"
    assert len((tmp_path / "log" / "actions.jsonl").read_text().splitlines()) == 1


def test_another_entry_with_the_same_identifier_is_journaled(tmp_path: Path) -> None:
    reception(tmp_path)
    earlier = {"prescription": request(), "result": answer("failed")}
    records.append_journal(tmp_path / "log", earlier, records.ACTIONS_NAME)
    state = State()
    records.start_action(tmp_path / "log", state, request(), LAUNCH, STARTED)

    records.finish_action(tmp_path / "log", tmp_path / "results", state, answer("done"), EPOCH)

    assert len((tmp_path / "log" / "actions.jsonl").read_text().splitlines()) == 2


def test_unwritable_public_log_does_not_stop_nurse_with_nothing_pending(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    service = reception(tmp_path)
    (tmp_path / "results" / "_log.json").mkdir()

    service.recover()

    assert "the public log cannot be written: IsADirectoryError" in caplog.text


def test_a_cut_line_does_not_swallow_the_next_one(tmp_path: Path) -> None:
    (tmp_path / "journal.jsonl").write_text('{"cut')

    records.append_journal(tmp_path, {"prescription": None, "result": {}})

    assert (tmp_path / "journal.jsonl").read_text().splitlines()[1].startswith('{"prescription"')


@pytest.mark.parametrize("pending", [True, False])
def test_identifier_of_a_known_action_gets_no_other_answer(tmp_path: Path, pending: bool) -> None:
    interrupted(tmp_path)
    service = reception(tmp_path, allow, clock=lambda: NOW)
    if not pending:
        service.recover()
        (tmp_path / "results" / "rx-1.json").unlink()
    state = load_state(tmp_path / "log")
    state.handled.clear()  # long forgotten
    records.save_state(tmp_path / "log", state)
    drop(tmp_path, request())

    service.run_once()

    assert not (tmp_path / "results" / "rx-1.json").exists()
    assert "duplicate" in [entry["result"]["refusal_reason"] for entry in log(tmp_path)]


def test_result_of_a_pending_action_does_not_count_in_the_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(records, "MAX_RESULTS", 0)
    interrupted(tmp_path)
    (tmp_path / "results" / "rx-1.json").write_text(json.dumps(answer("done")))

    reception(tmp_path, allow).run_once()

    assert (tmp_path / "results" / "rx-1.json").exists()


def test_result_of_an_action_goes_after_7_days(tmp_path: Path) -> None:
    path = tmp_path / "rx-1.json"
    path.write_text("{}")
    os.utime(path, (EPOCH - 8 * 86400,) * 2)

    records.prune_results(tmp_path, EPOCH, frozenset({"rx-1"}))

    assert not path.exists()


def test_only_the_last_80_actions_stay_in_the_state(tmp_path: Path) -> None:
    reception(tmp_path)
    state = State()
    for number in range(85):
        rx_id = f"a{number}"
        records.start_action(tmp_path / "log", state, request(rx_id), LAUNCH, STARTED)
        done = answer("done", rx_id=rx_id)
        records.finish_action(tmp_path / "log", tmp_path / "results", state, done, EPOCH)

    assert len(load_state(tmp_path / "log").actions) == 80


@pytest.mark.parametrize(("number", "usable"), [("12345.5", True), ("1e999", False)])
def test_number_too_large_to_be_written_back_makes_the_state_unusable(
    tmp_path: Path, number: str, usable: bool
) -> None:
    pending = {"a": {"prescription": {"rx_id": "a", "n": 0}, "started_at": "x"}}
    content = with_state(pending=pending).replace(b'"n": 0', b'"n": ' + number.encode())
    (tmp_path / "state.json").write_bytes(content)
    (tmp_path / "started.json").write_text(json.dumps({"a": EPOCH}))

    if usable:
        assert list(load_state(tmp_path).pending) == ["a"]
    else:
        with pytest.raises(StateError):
            load_state(tmp_path)


def test_ended_actions_alone_need_their_identifiers(tmp_path: Path) -> None:
    reception(tmp_path)
    state = State()
    started_and_done(tmp_path, state, "a", EPOCH)
    (tmp_path / "log" / "started.json").unlink()

    with pytest.raises(StateError):
        load_state(tmp_path / "log")


@pytest.mark.parametrize("failing", ["started.json", "state.json"])
def test_identifier_is_written_before_the_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failing: str
) -> None:
    write = records._write_atomically

    def fail_one(path: Path, value: object, mode: int) -> None:
        if path.name == failing:
            raise PermissionError
        write(path, value, mode)

    monkeypatch.setattr(records, "_write_atomically", fail_one)

    with pytest.raises(PermissionError):
        records.start_action(tmp_path, State(), request(), LAUNCH, STARTED)

    state = load_state(tmp_path)
    assert state.pending == {}
    assert list(state.started) == (["rx-1"] if failing == "state.json" else [])


def test_late_recovery_remembers_the_identifier_as_long_as_the_result(tmp_path: Path) -> None:
    interrupted(tmp_path)
    later = NOW + timedelta(days=3)

    reception(tmp_path, clock=lambda: later).recover()

    assert load_state(tmp_path / "log").started == {"rx-1": int(later.timestamp())}


def test_oversized_number_in_a_result_is_not_kept_at_recovery(tmp_path: Path) -> None:
    interrupted(tmp_path)
    text = json.dumps(answer("done", exit_code=0)).replace('"exit_code": 0', '"exit_code": 1e999')
    (tmp_path / "results" / "rx-1.json").write_text(text)

    reception(tmp_path).recover()

    assert result(tmp_path)["status"] == "unverified"


def test_recovery_goes_on_when_only_the_public_log_fails(tmp_path: Path) -> None:
    reception(tmp_path)
    state = State()
    for rx_id in ("a", "b"):
        records.start_action(tmp_path / "log", state, request(rx_id), LAUNCH, STARTED)
    (tmp_path / "results" / "_log.json").mkdir()

    reception(tmp_path).recover()

    assert load_state(tmp_path / "log").pending == {}


def running_with_an_action(tmp_path: Path) -> Reception:
    """A NURSE that started with one pending action and has no result for it yet."""
    interrupted(tmp_path)
    return reception(tmp_path, allow)


@pytest.mark.parametrize("pending", [True, False])
def test_unusable_state_never_answers_for_an_action(tmp_path: Path, pending: bool) -> None:
    service = running_with_an_action(tmp_path)
    if not pending:
        service.recover()
        (tmp_path / "results" / "rx-1.json").unlink()
    (tmp_path / "log" / "state.json").write_bytes(b"{")
    drop(tmp_path, request())
    drop(tmp_path, request("other"), "other.json")

    service.run_once()

    assert not (tmp_path / "results" / "rx-1.json").exists()
    assert result(tmp_path, "other")["refusal_reason"] == "state_unreadable"
    assert [line["result"]["rx_id"] for line in journal(tmp_path)] == ["other", "rx-1"]
    assert list((tmp_path / "prescriptions").iterdir()) == []


@pytest.mark.parametrize("damage", [None, b"{", b"null", b'{"../x": 1}'])
def test_nothing_is_written_when_the_actions_cannot_be_known(
    tmp_path: Path, damage: bytes | None
) -> None:
    service = running_with_an_action(tmp_path)
    (tmp_path / "log" / "state.json").write_bytes(b"{")
    if damage is None:
        (tmp_path / "log" / "started.json").unlink()
    else:
        (tmp_path / "log" / "started.json").write_bytes(damage)
    drop(tmp_path, request("other"), "other.json")

    service.run_once()

    assert list((tmp_path / "results").iterdir()) == []
    assert [line["result"]["rx_id"] for line in journal(tmp_path)] == ["other"]


def test_full_state_never_answers_for_an_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = running_with_an_action(tmp_path)
    monkeypatch.setattr("nurse.reception.MAX_HANDLED", 1)
    monkeypatch.setattr(records, "HANDLED_SECONDS", 10**9)
    drop(tmp_path, request())
    drop(tmp_path, request("other"), "other.json")

    service.run_once()

    assert not (tmp_path / "results" / "rx-1.json").exists()
    assert result(tmp_path, "other")["refusal_reason"] == "state_unreadable"


def test_start_creates_the_identifiers_once(tmp_path: Path) -> None:
    service = reception(tmp_path)
    path = tmp_path / "log" / "started.json"
    path.unlink()
    service.recover()
    assert path.read_text() == "{}"
    path.write_text(json.dumps({"a": EPOCH}))

    reception(tmp_path).recover()

    assert json.loads(path.read_text()) == {"a": EPOCH}


def test_old_action_listed_in_the_state_keeps_its_protection(tmp_path: Path) -> None:
    service = reception(tmp_path, allow)
    state = State()
    started_and_done(tmp_path, state, "old", EPOCH - 9 * 86400)
    started_and_done(tmp_path, state, "new", EPOCH)
    (tmp_path / "results" / "old.json").unlink()  # removed after 7 days
    (tmp_path / "log" / "state.json").write_bytes(b"{")
    drop(tmp_path, request("old"), "old.json")

    service.run_once()

    assert not (tmp_path / "results" / "old.json").exists()


@pytest.mark.parametrize("both", [True, False])
def test_identifiers_lost_while_running_are_not_a_first_start(tmp_path: Path, both: bool) -> None:
    service = running_with_an_action(tmp_path)
    (tmp_path / "log" / "started.json").unlink()
    if both:
        (tmp_path / "log" / "state.json").unlink()
    else:
        (tmp_path / "log" / "state.json").write_bytes(with_state())
    drop(tmp_path, request())

    service.run_once()

    assert list((tmp_path / "results").iterdir()) == []
    assert [line["result"]["refusal_reason"] for line in journal(tmp_path)] == ["state_unreadable"]
    assert not (tmp_path / "log" / "started.json").exists()
