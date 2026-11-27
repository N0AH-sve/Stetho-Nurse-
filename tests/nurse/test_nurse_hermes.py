import json
import shutil
import sqlite3
import threading
from collections.abc import Callable
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from nurse import __main__ as program
from nurse import hermes
from nurse.hermes import HermesReader
from nurse.reception import Reception
from nurse.settings import load_catalogue, load_settings

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "hermes-0.21"
ACTIONS = load_catalogue(ROOT / "config" / "actions.example.toml")
NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
EPOCH = int(NOW.timestamp())
JOB = "111111111111"
TASK = "t_000000a1"


def build(path: Path, *scripts: Path) -> None:
    with closing(sqlite3.connect(path)) as connection:
        for script in scripts:
            connection.executescript(script.read_text(encoding="utf-8"))


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """The made-up degraded scenario, with its databases built and closed."""
    data = tmp_path / "hermes"
    shutil.copytree(FIXTURES / "degraded", data)
    schema = FIXTURES / "schema"
    build(
        data / "cron" / "executions.db",
        schema / "executions.sql",
        schema / "incidents.sql",
        data / "cron" / "executions.sql",
    )
    build(data / "kanban.db", schema / "kanban.sql", data / "kanban.sql")
    return data


def check(home: Path, code: str, target: str | None = None, now: datetime = NOW) -> str | None:
    return HermesReader(home)(ACTIONS[code], target, now)


def sql(path: Path, statement: str, *values: object) -> None:
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(statement, values)


def set_intent(home: Path, intent: object) -> None:
    path = home / "gateway_state.json"
    document = json.loads(path.read_text())
    document.pop("desired_state", None)
    if intent is not None:
        document["desired_state"] = intent
    path.write_text(json.dumps(document))


def set_job(home: Path, **changes: object) -> None:
    path = home / "cron" / "jobs.json"
    document = json.loads(path.read_text())
    document["jobs"][0].update(changes)
    path.write_text(json.dumps(document))


@pytest.mark.parametrize("code", ["RESTART_GATEWAY", "RESTART_CONTAINER"])
@pytest.mark.parametrize(
    ("intent", "expected"),
    [
        ("running", None),
        ("stopped", "not_relevant"),
        (None, "state_unreadable"),
        ("paused", "state_unreadable"),
        (3, "state_unreadable"),
    ],
)
def test_restart_needs_the_intent_to_run(
    home: Path, code: str, intent: object, expected: str | None
) -> None:
    set_intent(home, intent)

    assert check(home, code) == expected


@pytest.mark.parametrize("content", [None, b"", b"{", b"[]", b"[" * 100000, b" " * 4194305])
@pytest.mark.parametrize(
    ("name", "code", "target"),
    [("gateway_state.json", "RESTART_GATEWAY", None), ("cron/jobs.json", "PAUSE_JOB", JOB)],
)
def test_unusable_json_refuses(
    home: Path, name: str, code: str, target: str | None, content: bytes | None
) -> None:
    if content is None:
        (home / name).unlink()
    else:
        (home / name).write_bytes(content)

    assert check(home, code, target) == "state_unreadable"


@pytest.mark.parametrize("link", ["file", "folder"])
def test_nothing_is_read_through_a_link(home: Path, tmp_path: Path, link: str) -> None:
    if link == "file":
        (home / "cron" / "jobs.json").rename(tmp_path / "jobs.json")
        (home / "cron" / "jobs.json").symlink_to(tmp_path / "jobs.json")
    else:
        (home / "cron").rename(tmp_path / "cron")
        (home / "cron").symlink_to(tmp_path / "cron")

    assert check(home, "PAUSE_JOB", JOB) == "state_unreadable"


def test_data_folder_may_itself_be_a_link(home: Path, tmp_path: Path) -> None:
    (tmp_path / "mount").symlink_to(home)

    assert check(tmp_path / "mount", "PAUSE_JOB", JOB) is None


def test_missing_data_folder_refuses(tmp_path: Path) -> None:
    assert check(tmp_path / "absent", "PAUSE_JOB", JOB) == "state_unreadable"


@pytest.mark.parametrize("code", ["PAUSE_JOB", "RERUN_JOB"])
def test_enabled_job_that_is_not_running_may_be_handled(home: Path, code: str) -> None:
    assert check(home, code, JOB) is None


@pytest.mark.parametrize("code", ["PAUSE_JOB", "RERUN_JOB"])
def test_unknown_or_disabled_job_is_refused(home: Path, code: str) -> None:
    assert check(home, code, "999999999999") == "target_not_found"
    set_job(home, enabled=False)
    assert check(home, code, JOB) == "not_relevant"


def test_name_of_a_job_is_not_its_identifier(home: Path) -> None:
    set_job(home, name="abcdefabcdef")

    assert check(home, "PAUSE_JOB", "abcdefabcdef") == "target_not_found"


@pytest.mark.parametrize("claim", ["run_claim", "fire_claim"])
def test_claimed_job_is_not_run_again_but_may_be_paused(home: Path, claim: str) -> None:
    set_job(home, **{claim: {"at": "2026-01-15T11:59:00+00:00", "by": "sample"}})

    assert check(home, "RERUN_JOB", JOB) == "not_relevant"
    assert check(home, "PAUSE_JOB", JOB) is None


@pytest.mark.parametrize("status", ["claimed", "running"])
def test_job_with_an_unfinished_execution_is_not_run_again(home: Path, status: str) -> None:
    sql(home / "cron" / "executions.db", "UPDATE executions SET status = ?", status)

    assert check(home, "RERUN_JOB", JOB) == "not_relevant"


@pytest.mark.parametrize(
    "jobs", [None, {}, [1], [{"id": JOB, "enabled": 1}], [{"id": JOB, "enabled": True}] * 2]
)
def test_unexpected_job_list_refuses(home: Path, jobs: object) -> None:
    (home / "cron" / "jobs.json").write_text(json.dumps({"jobs": jobs}))

    assert check(home, "PAUSE_JOB", JOB) == "state_unreadable"


@pytest.mark.parametrize(
    "damage",
    [
        Path.unlink,
        lambda path: path.write_bytes(b"not a database" * 100),
        lambda path: sql(path, "DROP TABLE executions"),
    ],
)
def test_unusable_execution_database_refuses_a_rerun(
    home: Path, damage: Callable[[Path], Any]
) -> None:
    damage(home / "cron" / "executions.db")

    assert check(home, "RERUN_JOB", JOB) == "state_unreadable"


def test_silent_task_may_be_reclaimed(home: Path) -> None:
    assert check(home, "UNBLOCK_KANBAN_TASK", TASK) is None


def task(home: Path, started: int, beat: int | None, maximum: int | None) -> str | None:
    path = home / "kanban.db"
    sql(path, "UPDATE task_runs SET started_at = ? WHERE id = 1", started)
    sql(
        path,
        "UPDATE tasks SET last_heartbeat_at = ?, max_runtime_seconds = ? WHERE id = ?",
        beat,
        maximum,
        TASK,
    )
    return check(home, "UNBLOCK_KANBAN_TASK", TASK)


@pytest.mark.parametrize(
    ("started", "beat", "maximum", "expected"),
    [
        (EPOCH - 3000, EPOCH - 900, None, "not_relevant"),
        (EPOCH - 3000, EPOCH - 901, None, None),
        (EPOCH - 900, None, None, "not_relevant"),
        (EPOCH - 901, None, None, None),
        # A heartbeat of an earlier attempt does not make a new one silent.
        (EPOCH - 60, EPOCH - 5000, None, "not_relevant"),
        (EPOCH - 3600, EPOCH - 10, 3600, "not_relevant"),
        (EPOCH - 3601, EPOCH - 10, 3600, None),
        (EPOCH - 3601, EPOCH - 10, 0, "not_relevant"),
        (EPOCH + 9000, None, 10, "not_relevant"),
    ],
)
def test_only_a_silent_or_too_long_task_may_be_reclaimed(
    home: Path, started: int, beat: int | None, maximum: int | None, expected: str | None
) -> None:
    assert task(home, started, beat, maximum) == expected


def test_unknown_task_or_task_not_running_is_refused(home: Path) -> None:
    assert check(home, "UNBLOCK_KANBAN_TASK", "t_ffffffff") == "target_not_found"
    assert check(home, "UNBLOCK_KANBAN_TASK", "t_000000a2") == "not_relevant"


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE tasks SET current_run_id = NULL",
        "UPDATE tasks SET last_heartbeat_at = 'text'",
        "UPDATE tasks SET max_runtime_seconds = 1.5",
        "DROP TABLE task_runs",
    ],
)
def test_unexpected_task_data_refuses(home: Path, statement: str) -> None:
    sql(home / "kanban.db", statement)

    assert check(home, "UNBLOCK_KANBAN_TASK", TASK) == "state_unreadable"


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"default\n", None),
        (b"other", "not_relevant"),
        (b"", "state_unreadable"),
        (b"x" * 300, "state_unreadable"),
    ],
)
def test_only_the_default_board_is_handled(
    home: Path, content: bytes, expected: str | None
) -> None:
    (home / "kanban").mkdir()
    (home / "kanban" / "current").write_bytes(content)

    assert check(home, "UNBLOCK_KANBAN_TASK", TASK) == expected


def test_action_with_a_target_refuses_without_one(home: Path) -> None:
    assert check(home, "PAUSE_JOB") == "state_unreadable"


def snapshot(home: Path) -> dict[str, tuple[bytes, int]]:
    return {
        str(path.relative_to(home)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in home.rglob("*")
        if path.is_file()
    }


def test_nothing_is_written_in_hermes(home: Path) -> None:
    sql(home / "kanban.db", "PRAGMA journal_mode=WAL")  # idle: no side file is left
    before = snapshot(home)

    check(home, "RESTART_GATEWAY")
    check(home, "RERUN_JOB", JOB)
    assert check(home, "UNBLOCK_KANBAN_TASK", TASK) is None

    assert snapshot(home) == before


def test_database_in_use_is_read_with_its_latest_changes(home: Path) -> None:
    with closing(sqlite3.connect(home / "kanban.db")) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        with writer:
            writer.execute("UPDATE tasks SET status = 'done'")

        assert check(home, "UNBLOCK_KANBAN_TASK", TASK) == "not_relevant"


def test_idle_database_changed_during_the_read_is_not_trusted(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identities = iter([(1, 1, 1), (1, 1, 2)])
    monkeypatch.setattr(hermes, "_identity", lambda _: next(identities))

    assert check(home, "UNBLOCK_KANBAN_TASK", TASK) == "state_unreadable"


def test_program_checks_requests_against_hermes(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folders = {name: tmp_path / name for name in ("prescriptions", "results", "log")}
    for folder in folders.values():
        folder.mkdir()
    settings = replace(
        load_settings(ROOT / "config" / "nurse.example.toml"),
        data_dir=home,
        prescriptions_dir=folders["prescriptions"],
        results_dir=folders["results"],
        log_dir=folders["log"],
    )
    request = {
        "rx_id": "rx-1",
        "action": "PAUSE_JOB",
        "target": JOB,
        "origin": "manual",
        "requested_by": "shared",
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    (folders["prescriptions"] / "rx-1.json").write_text(json.dumps(request))

    def one_pass(reception: Reception, _stop: threading.Event) -> None:
        reception.run_once()

    monkeypatch.setattr(program, "load_settings", lambda _: settings)
    monkeypatch.setattr(program, "load_catalogue", lambda _: ACTIONS)
    monkeypatch.setattr(program, "watch", one_pass)
    monkeypatch.setattr("signal.signal", lambda *_: None)

    assert program.main(["--config", "unused"]) == 0

    result = json.loads((folders["results"] / "rx-1.json").read_text())
    assert result["refusal_reason"] == "execution_disabled"


def test_uncommitted_changes_are_never_trusted(home: Path) -> None:
    database = home / "kanban.db"
    with closing(sqlite3.connect(database)) as writer:
        writer.execute("PRAGMA journal_mode=DELETE")
        writer.execute("PRAGMA cache_size=1")
        writer.execute("UPDATE tasks SET last_heartbeat_at = NULL")

        assert (home / "kanban.db-journal").exists()
        assert check(home, "UNBLOCK_KANBAN_TASK", TASK) == "state_unreadable"
        writer.rollback()


@pytest.mark.parametrize("suffix", ["-journal", "-wal", "-shm"])
@pytest.mark.parametrize("kind", ["broken link", "folder"])
def test_unexpected_side_file_refuses(home: Path, suffix: str, kind: str) -> None:
    side = home / f"kanban.db{suffix}"
    if kind == "folder":
        side.mkdir()
    else:
        side.symlink_to(home / "absent")

    assert check(home, "UNBLOCK_KANBAN_TASK", TASK) == "state_unreadable"


@pytest.mark.parametrize("name", ["kanban", "kanban/current"])
def test_broken_link_to_the_board_is_not_the_default_board(home: Path, name: str) -> None:
    link = home / name
    link.parent.mkdir(exist_ok=True)
    link.symlink_to(home / "absent")

    assert check(home, "UNBLOCK_KANBAN_TASK", TASK) == "state_unreadable"


@pytest.mark.parametrize("suffix", ["-wal", "-journal"])
def test_writer_arriving_during_an_idle_read_is_noticed(
    home: Path, monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    identity = hermes._identity

    def arrive(path: Path) -> tuple[int, int, int]:
        found = identity(path)
        path.with_name(path.name + suffix).touch()
        return found

    monkeypatch.setattr(hermes, "_identity", arrive)

    assert check(home, "UNBLOCK_KANBAN_TASK", TASK) == "state_unreadable"
