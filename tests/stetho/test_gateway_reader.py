import json
import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from stetho.anomalies import HIDDEN, Anomaly, AnomalyCode, UnreadableReason, describe
from stetho.config import HermesSettings, Language
from stetho.i18n import load_translator
from stetho.modules.gateway.reader import (
    GatewayReading,
    GatewaysReading,
    load_adapter,
    read_gateway,
    read_gateways,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "hermes-0.21"
NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)  # the reference time of the fixtures
ADAPTER = load_adapter()
MARKER = "MADE-UP-private-text with spaces"


def no_sleep(_: float) -> None:
    pass


def read(home: Path, profile: str = "default") -> GatewayReading:
    return read_gateway(home, profile, adapter=ADAPTER, now=NOW, sleep=no_sleep)


def healthy_document(**changes: Any) -> dict[str, Any]:
    content = (FIXTURES / "healthy" / "gateway_state.json").read_text(encoding="utf-8")
    document: dict[str, Any] = json.loads(content)
    return document | changes


def write_state(home: Path, document: object) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    (home / "gateway_state.json").write_text(json.dumps(document), encoding="utf-8")
    return home


def write_log(home: Path, *lines: str, end: str = "\n") -> Path:
    (home / "logs").mkdir(parents=True, exist_ok=True)
    (home / "logs" / "gateway-exit-diag.log").write_text("\n".join(lines) + end, encoding="utf-8")
    return home


def unclean(ts: object = "2026-01-15T11:30:02+00:00", **extra: object) -> str:
    return json.dumps({"ts": ts, "tag": "gateway.previous_unclean_exit"} | extra)


def codes(anomalies: tuple[Anomaly, ...]) -> list[tuple[AnomalyCode, str | None]]:
    return [(item.code, item.field) for item in anomalies]


def test_healthy_gateway() -> None:
    reading = read(FIXTURES / "healthy")

    assert reading.anomalies == ()
    assert reading.last_unclean_stop is None
    state = reading.state
    assert state is not None
    assert (state.state, state.desired_state, state.exit_reason) == ("running", None, None)
    assert state.heartbeat is not None
    assert NOW - state.heartbeat.at == timedelta(seconds=30)
    assert not state.heartbeat.written_by_command
    assert (state.active_agents, state.hermes_version) == (0, "0.21.4")


def test_degraded_gateway_with_unclean_previous_stop() -> None:
    reading = read(FIXTURES / "degraded")

    assert reading.state is not None
    assert reading.state.state == "degraded"
    assert reading.state.served_profiles == ("work",)
    assert reading.last_unclean_stop is not None
    assert reading.last_unclean_stop.detected_at == datetime(2026, 1, 15, 11, 30, 2, tzinfo=UTC)
    assert reading.last_unclean_stop.suspected_oom
    assert reading.anomalies == ()


@pytest.mark.parametrize(
    ("scenario", "expected"), [("stopped", "stopped"), ("startup-failed", "startup_failed")]
)
def test_stopped_gateways(scenario: str, expected: str) -> None:
    reading = read(FIXTURES / scenario)

    assert reading.state is not None
    assert reading.state.state == expected
    assert reading.anomalies == ()


def test_free_text_reason_never_leaves_the_reader() -> None:
    reading = read(FIXTURES / "startup-failed")

    assert reading.state is not None
    assert reading.state.exit_reason is None
    assert reading.state.has_other_exit_reason
    assert "Made-up startup error" not in repr(reading)


@pytest.mark.parametrize("reason", ["loop_liveness_watchdog", "shutdown_watchdog"])
def test_known_reason_is_kept(tmp_path: Path, reason: str) -> None:
    state = read(write_state(tmp_path, healthy_document(exit_reason=reason))).state

    assert state is not None
    assert (state.exit_reason, state.has_other_exit_reason) == (reason, False)


def test_date_written_by_a_command_is_marked() -> None:
    reading = read(FIXTURES / "invalid" / "restart-written-by-cli")

    assert reading.state is not None
    assert reading.state.heartbeat is not None
    assert reading.state.heartbeat.written_by_command
    assert reading.state.heartbeat.at == NOW - timedelta(seconds=5)
    assert reading.state.desired_state == "running"
    assert reading.anomalies == ()


def test_unknown_state_is_reported_with_the_expected_ones() -> None:
    reading = read(FIXTURES / "invalid" / "unknown-value")

    assert reading.state is not None
    assert reading.state.state is None
    (anomaly,) = reading.anomalies
    assert (anomaly.code, anomaly.field) == (AnomalyCode.UNEXPECTED_VALUE, "gateway_state")
    assert anomaly.received == "hibernating"
    assert anomaly.expected == ADAPTER.gateway_states
    assert "hibernating" in describe(anomaly, load_translator(Language.FR))


@pytest.mark.parametrize("value", [MARKER, None, 3, ["running"], {"a": MARKER}])
def test_unusual_state_value_is_filtered(tmp_path: Path, value: object) -> None:
    reading = read(write_state(tmp_path, healthy_document(gateway_state=value)))

    assert reading.state is not None
    assert reading.state.state is None
    assert codes(reading.anomalies) == [(AnomalyCode.UNEXPECTED_VALUE, "gateway_state")]
    assert MARKER not in repr(reading)
    assert reading.anomalies[0].received in (HIDDEN, "null", "3")


def test_missing_required_field_makes_the_state_unusable() -> None:
    reading = read(FIXTURES / "invalid" / "missing-field")

    assert reading.state is None
    assert codes(reading.anomalies) == [(AnomalyCode.SCHEMA_CHANGED, "gateway_state")]


def test_first_file_of_a_new_container_is_unusable(tmp_path: Path) -> None:
    reading = read(write_state(tmp_path, {"gateway_state": "running"}))

    assert reading.state is None
    assert codes(reading.anomalies) == [(AnomalyCode.SCHEMA_CHANGED, "updated_at")]


def test_wrong_types_never_give_a_usable_date() -> None:
    # `updated_at` is 12: the integer form, but long before any Hermes.
    reading = read(FIXTURES / "invalid" / "wrong-type")

    assert reading.state is not None
    assert reading.state.heartbeat is None
    assert codes(reading.anomalies) == [(AnomalyCode.IMPLAUSIBLE_VALUE, "updated_at")]


def test_future_date_is_not_used() -> None:
    reading = read(FIXTURES / "invalid" / "future-date")

    assert reading.state is not None
    assert reading.state.heartbeat is None
    assert codes(reading.anomalies) == [(AnomalyCode.IMPLAUSIBLE_VALUE, "updated_at")]
    assert reading.anomalies[0].received == "2026-01-16T12:00:00+00:00"


@pytest.mark.parametrize(("ahead", "accepted"), [(300, True), (301, False)])
def test_future_tolerance_is_five_minutes(tmp_path: Path, ahead: int, accepted: bool) -> None:
    date = (NOW + timedelta(seconds=ahead)).isoformat()

    state = read(write_state(tmp_path, healthy_document(updated_at=date))).state

    assert state is not None
    assert (state.heartbeat is not None) is accepted


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("yesterday", AnomalyCode.SCHEMA_CHANGED),
        ("2026-01-15T11:59:30", AnomalyCode.SCHEMA_CHANGED),  # no time zone
        (1768478395.5, AnomalyCode.SCHEMA_CHANGED),
        (True, AnomalyCode.SCHEMA_CHANGED),
        (None, AnomalyCode.SCHEMA_CHANGED),
        (10**30, AnomalyCode.IMPLAUSIBLE_VALUE),
        (-(10**12), AnomalyCode.IMPLAUSIBLE_VALUE),
        ("2001-01-01T00:00:00+00:00", AnomalyCode.IMPLAUSIBLE_VALUE),
    ],
)
def test_unusable_date_is_reported(tmp_path: Path, value: object, code: AnomalyCode) -> None:
    reading = read(write_state(tmp_path, healthy_document(updated_at=value)))

    assert reading.state is not None
    assert reading.state.heartbeat is None
    assert codes(reading.anomalies) == [(code, "updated_at")]


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"active_agents": -1}, (AnomalyCode.IMPLAUSIBLE_VALUE, "active_agents")),
        ({"active_agents": "2"}, (AnomalyCode.SCHEMA_CHANGED, "active_agents")),
        ({"active_agents": True}, (AnomalyCode.SCHEMA_CHANGED, "active_agents")),
        ({"served_profiles": "work"}, (AnomalyCode.SCHEMA_CHANGED, "served_profiles")),
        ({"served_profiles": ["../x"]}, (AnomalyCode.SCHEMA_CHANGED, "served_profiles")),
        ({"exit_reason": 3}, (AnomalyCode.SCHEMA_CHANGED, "exit_reason")),
        ({"new_field": MARKER}, (AnomalyCode.SCHEMA_CHANGED, "new_field")),
        ({MARKER: 1}, (AnomalyCode.SCHEMA_CHANGED, "<hidden>")),
    ],
)
def test_other_problems_keep_the_state_usable(
    tmp_path: Path, changes: dict[str, Any], expected: tuple[AnomalyCode, str]
) -> None:
    reading = read(write_state(tmp_path, healthy_document(**changes)))

    assert reading.state is not None
    assert reading.state.state == "running"
    assert codes(reading.anomalies) == [expected]
    assert MARKER not in repr(reading)


@pytest.mark.parametrize("value", ["paused", None, 1])
def test_unknown_intent_is_marked(tmp_path: Path, value: object) -> None:
    state = read(write_state(tmp_path, healthy_document(desired_state=value))).state

    assert state is not None
    assert (state.desired_state, state.desired_state_unrecognised) == (None, True)


@pytest.mark.parametrize("value", [None, 3, MARKER])
def test_unusable_version_is_left_out(tmp_path: Path, value: object) -> None:
    reading = read(write_state(tmp_path, healthy_document(code_version=value)))

    assert reading.state is not None
    assert reading.state.hermes_version is None
    assert reading.anomalies == ()


def test_only_required_fields_are_enough(tmp_path: Path) -> None:
    document = {"gateway_state": "running", "updated_at": "2026-01-15T11:59:30+00:00"}

    reading = read(write_state(tmp_path, document))

    assert reading.state is not None
    assert reading.anomalies == ()


def test_truncated_file_is_a_parse_error() -> None:
    reading = read(FIXTURES / "invalid" / "truncated-json")

    assert reading.state is None
    assert codes(reading.anomalies) == [(AnomalyCode.PARSE_ERROR, None)]


@pytest.mark.parametrize(
    "content", [b"", b"[]", b'"x"', b"\xff", b"[" * 100_000, b'{"a":' + b"9" * 10_000 + b"}"]
)
def test_other_invalid_content_is_a_parse_error(tmp_path: Path, content: bytes) -> None:
    (tmp_path / "gateway_state.json").write_bytes(content)

    assert codes(read(tmp_path).anomalies) == [(AnomalyCode.PARSE_ERROR, None)]


def test_missing_state_file(tmp_path: Path) -> None:
    reading = read(tmp_path)

    assert reading.state is None
    (anomaly,) = reading.anomalies
    assert (anomaly.code, anomaly.source) == (AnomalyCode.SOURCE_MISSING, "gateway_state.json")


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read every file")
def test_unreadable_state_file(tmp_path: Path) -> None:
    write_state(tmp_path, healthy_document())
    (tmp_path / "gateway_state.json").chmod(0)

    (anomaly,) = read(tmp_path).anomalies

    assert anomaly.reason is UnreadableReason.PERMISSION_DENIED


def test_latest_unclean_stop_is_kept(tmp_path: Path) -> None:
    write_state(tmp_path, healthy_document())
    write_log(
        tmp_path,
        unclean("2026-01-14T10:00:00+00:00", suspected_oom=True),
        "",
        unclean("2026-01-15T10:00:00+00:00"),
        json.dumps({"tag": "asyncio.run.exception", "traceback": MARKER, "argv": [MARKER]}),
    )

    reading = read(tmp_path)

    assert reading.last_unclean_stop is not None
    assert reading.last_unclean_stop.detected_at == datetime(2026, 1, 15, 10, tzinfo=UTC)
    assert not reading.last_unclean_stop.suspected_oom
    assert MARKER not in repr(reading)


def test_line_being_written_is_ignored() -> None:
    reading = read(FIXTURES / "invalid" / "partial-log-line")

    assert codes(reading.anomalies) == [(AnomalyCode.SOURCE_MISSING, None)]  # no state file
    assert reading.anomalies[0].source == "gateway_state.json"


def test_broken_line_is_reported_and_others_are_read(tmp_path: Path) -> None:
    write_state(tmp_path, healthy_document())
    write_log(tmp_path, f"{MARKER} not json", "[1]", unclean())

    reading = read(tmp_path)

    assert reading.state is not None
    assert reading.last_unclean_stop is not None
    (anomaly,) = reading.anomalies
    assert (anomaly.code, anomaly.source) == (
        AnomalyCode.PARSE_ERROR,
        "logs/gateway-exit-diag.log",
    )
    assert MARKER not in repr(reading)


@pytest.mark.parametrize(
    ("ts", "code"),
    [
        (None, AnomalyCode.SCHEMA_CHANGED),
        (MARKER, AnomalyCode.SCHEMA_CHANGED),
        ("2027-01-01T00:00:00+00:00", AnomalyCode.IMPLAUSIBLE_VALUE),
    ],
)
def test_unusable_date_of_unclean_stop(tmp_path: Path, ts: object, code: AnomalyCode) -> None:
    write_log(write_state(tmp_path, healthy_document()), unclean(ts))

    reading = read(tmp_path)

    assert reading.last_unclean_stop is None
    assert codes(reading.anomalies) == [(code, "ts")]
    assert MARKER not in repr(reading)


def test_only_the_end_of_a_large_log_is_read(tmp_path: Path) -> None:
    write_state(tmp_path, healthy_document())
    old = unclean("2026-01-10T00:00:00+00:00")
    filler = [json.dumps({"tag": "atexit.hook", "pad": "x" * 1000})] * 300
    write_log(tmp_path, old, *filler)
    assert read(tmp_path).last_unclean_stop is None

    write_log(tmp_path, old, *filler, unclean())

    reading = read(tmp_path)
    assert reading.last_unclean_stop is not None
    assert reading.anomalies == ()


def test_unreadable_log_keeps_the_state(tmp_path: Path) -> None:
    write_state(tmp_path, healthy_document())
    (tmp_path / "logs" / "gateway-exit-diag.log").mkdir(parents=True)

    reading = read(tmp_path)

    assert reading.state is not None
    assert codes(reading.anomalies) == [(AnomalyCode.SOURCE_UNREADABLE, None)]


def test_named_profile_is_read_from_its_folder(tmp_path: Path) -> None:
    write_state(tmp_path / "profiles" / "work", {"gateway_state": "running"})

    reading = read(tmp_path, "work")

    assert reading.profile == "work"
    assert reading.anomalies[0].source == "profiles/work/gateway_state.json"


def test_reading_modifies_nothing(tmp_path: Path) -> None:
    home = tmp_path / "home"
    shutil.copytree(FIXTURES / "degraded", home)

    def snapshot() -> dict[str, tuple[int, int]]:
        return {
            str(path): (path.stat().st_mtime_ns, path.stat().st_size) for path in home.rglob("*")
        }

    before = snapshot()
    read(home)

    assert snapshot() == before


def read_all(data_dir: Path, profiles: Any = "auto") -> GatewaysReading:
    settings = HermesSettings(data_dir=data_dir, profiles=profiles, profile_labels={})
    return read_gateways(settings, adapter=ADAPTER, now=NOW, sleep=no_sleep)


def names(result: GatewaysReading) -> list[str]:
    return [reading.profile for reading in result.gateways]


def test_main_profile_is_always_expected(tmp_path: Path) -> None:
    result = read_all(tmp_path)

    assert names(result) == ["default"]
    assert codes(result.gateways[0].anomalies) == [(AnomalyCode.SOURCE_MISSING, None)]


def test_other_profiles_need_a_state_file(tmp_path: Path) -> None:
    write_state(tmp_path, healthy_document())
    write_state(tmp_path / "profiles" / "work", healthy_document())
    write_state(tmp_path / "profiles" / "alpha", healthy_document())
    write_state(tmp_path / "profiles" / "default", healthy_document())
    (tmp_path / "profiles" / "no-gateway").mkdir()
    (tmp_path / "profiles" / "a-file").write_text("x", encoding="utf-8")

    result = read_all(tmp_path)

    assert names(result) == ["default", "alpha", "work"]
    assert result.anomalies == ()


def test_unusable_profile_folder_name_is_reported(tmp_path: Path) -> None:
    write_state(tmp_path / "profiles" / MARKER, healthy_document())

    result = read_all(tmp_path)

    assert names(result) == ["default"]
    assert codes(result.anomalies) == [(AnomalyCode.SCHEMA_CHANGED, "profile name")]
    assert MARKER not in repr(result)


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read every folder")
def test_unlistable_profiles_folder_is_reported(tmp_path: Path) -> None:
    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles").chmod(0)
    try:
        result = read_all(tmp_path)
    finally:
        (tmp_path / "profiles").chmod(0o700)

    (anomaly,) = result.anomalies
    assert (anomaly.source, anomaly.reason) == ("profiles", UnreadableReason.PERMISSION_DENIED)


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read every folder")
def test_closed_profile_folder_is_reported(tmp_path: Path) -> None:
    closed = write_state(tmp_path / "profiles" / "work", healthy_document())
    closed.chmod(0)
    try:
        result = read_all(tmp_path)
    finally:
        closed.chmod(0o700)

    assert names(result) == ["default", "work"]
    assert result.gateways[1].anomalies[0].code is AnomalyCode.SOURCE_UNREADABLE


def test_listed_profile_is_always_expected(tmp_path: Path) -> None:
    write_state(tmp_path, healthy_document())

    result = read_all(tmp_path, ("default", "work"))

    assert names(result) == ["default", "work"]
    assert codes(result.gateways[1].anomalies) == [(AnomalyCode.SOURCE_MISSING, None)]


def test_served_profile_needs_no_file_of_its_own(tmp_path: Path) -> None:
    write_state(tmp_path, healthy_document(served_profiles=["work"]))

    assert names(read_all(tmp_path, ("default", "work"))) == ["default"]
    assert names(read_all(tmp_path, ("work",))) == []

    write_state(tmp_path / "profiles" / "work", healthy_document())

    assert names(read_all(tmp_path, ("work",))) == ["work"]


@pytest.mark.parametrize(
    "content",
    ["[]\n", "required_fields: []\n"],
)
def test_incomplete_adapter_is_refused(tmp_path: Path, content: str) -> None:
    (tmp_path / "adapter.yaml").write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match="invalid gateway adapter"):
        load_adapter(tmp_path / "adapter.yaml")


def test_adapter_values_must_be_lists_of_texts(tmp_path: Path) -> None:
    content = "".join(f"{name}: [1]\n" for name in ADAPTER.__dataclass_fields__)
    (tmp_path / "adapter.yaml").write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match="must be a list of texts"):
        load_adapter(tmp_path / "adapter.yaml")
