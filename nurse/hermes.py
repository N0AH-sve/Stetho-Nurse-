"""Reads Hermes, without ever writing, to tell whether an action makes sense now.

Only the main profile and the default Kanban board are read. Anything that cannot be read or
understood refuses the action. No content is selected: no name, prompt, title or error.
"""

import json
import sqlite3
import stat
import time
from collections.abc import Sequence
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

from nurse.settings import Action

_MAX_JSON_BYTES = 4 * 1024 * 1024
_MAX_BOARD_BYTES = 256
_QUERY_SECONDS = 2.0
_SILENT_AFTER = timedelta(minutes=15).total_seconds()
_UNREADABLE = "state_unreadable"
_NOT_FOUND = "target_not_found"
_NOT_RELEVANT = "not_relevant"


class _UnreadableError(Exception):
    """Something cannot be read or understood. It carries no detail on purpose."""


def _file(data_dir: Path, name: str) -> Path | None:
    """A regular file of the data folder, reached without any link below it, or `None`."""
    try:
        root = data_dir.resolve(strict=True)
        path = root / name
        found = path.lstat()
        if not stat.S_ISREG(found.st_mode) or path.resolve(strict=True) != path:
            raise _UnreadableError
    except FileNotFoundError:
        return None
    except (OSError, RuntimeError):
        raise _UnreadableError from None
    return path


def _read(path: Path, limit: int) -> bytes:
    try:
        with path.open("rb") as stream:
            content = stream.read(limit + 1)
    except OSError:
        raise _UnreadableError from None
    if len(content) > limit:
        raise _UnreadableError
    return content


def _json_object(data_dir: Path, name: str) -> dict[str, object]:
    path = _file(data_dir, name)
    if path is None:
        raise _UnreadableError
    try:
        document = json.loads(_read(path, _MAX_JSON_BYTES))
    except (ValueError, RecursionError):
        raise _UnreadableError from None
    if not isinstance(document, dict):
        raise _UnreadableError
    return document


def _identity(path: Path) -> tuple[int, int, int]:
    found = path.stat()
    return found.st_ino, found.st_size, found.st_mtime_ns


def _select(
    data_dir: Path, name: str, query: str, values: Sequence[str]
) -> list[tuple[object, ...]]:
    """At most two rows of a database that Hermes may be using."""
    path = _file(data_dir, name)
    if path is None:
        raise _UnreadableError
    side_files = [path.with_name(path.name + suffix) for suffix in ("-wal", "-shm")]
    try:
        # `immutable` only for an idle database: `mode=ro` fails on some of them.
        idle = not any(side.exists() for side in side_files)
        before = _identity(path)
        uri = f"{path.as_uri()}?mode=ro" + ("&immutable=1" if idle else "")
        deadline = time.monotonic() + _QUERY_SECONDS
        with closing(sqlite3.connect(uri, uri=True, timeout=_QUERY_SECONDS)) as connection:
            connection.set_progress_handler(lambda: time.monotonic() > deadline, 1000)
            rows = connection.execute(query, values).fetchmany(2)
        # A writer that came during an idle read may have made it wrong.
        if idle and (_identity(path) != before or any(side.exists() for side in side_files)):
            raise _UnreadableError
    except (OSError, sqlite3.Error):
        raise _UnreadableError from None
    return rows


def _gateway(data_dir: Path) -> str | None:
    intent = _json_object(data_dir, "gateway_state.json").get("desired_state")
    if intent == "running":
        return None
    if intent == "stopped":
        return _NOT_RELEVANT
    raise _UnreadableError  # absent or unknown: it could be a deliberate stop


def _job(data_dir: Path, target: str, must_be_idle: bool) -> str | None:
    jobs = _json_object(data_dir, "cron/jobs.json").get("jobs")
    if not isinstance(jobs, list) or not all(isinstance(job, dict) for job in jobs):
        raise _UnreadableError
    found = [job for job in jobs if job.get("id") == target]
    if not found:
        return _NOT_FOUND
    if len(found) > 1 or not isinstance(found[0].get("enabled"), bool):
        raise _UnreadableError
    job = found[0]
    if not job["enabled"]:
        return _NOT_RELEVANT
    if not must_be_idle:
        return None
    if job.get("run_claim") is not None or job.get("fire_claim") is not None:
        return _NOT_RELEVANT
    running = _select(
        data_dir,
        "cron/executions.db",
        "SELECT 1 FROM executions WHERE job_id = ? AND status IN ('claimed', 'running') LIMIT 1",
        (target,),
    )
    return _NOT_RELEVANT if running else None


def _check_board(data_dir: Path) -> str | None:
    """A command without a board acts on the active one: only the default one is handled."""
    path = _file(data_dir, "kanban/current")
    if path is None:
        return None
    content = _read(path, _MAX_BOARD_BYTES).strip()
    if not content:
        raise _UnreadableError
    return None if content == b"default" else _NOT_RELEVANT


def _time(value: object) -> int | None:
    if value is None or (isinstance(value, int) and not isinstance(value, bool)):
        return value
    raise _UnreadableError


def _task(data_dir: Path, target: str, now: datetime) -> str | None:
    refused = _check_board(data_dir)
    if refused:
        return refused
    # The current attempt: the task itself may keep the start of an earlier one.
    rows = _select(
        data_dir,
        "kanban.db",
        "SELECT t.status, r.started_at, t.last_heartbeat_at, t.max_runtime_seconds "
        "FROM tasks AS t LEFT JOIN task_runs AS r "
        "ON r.id = t.current_run_id AND r.task_id = t.id WHERE t.id = ?",
        (target,),
    )
    if not rows:
        return _NOT_FOUND
    if len(rows) > 1:
        raise _UnreadableError
    status, started, heartbeat, maximum = rows[0]
    if status != "running":
        return _NOT_RELEVANT
    began, beat, limit = _time(started), _time(heartbeat), _time(maximum)
    if began is None:
        raise _UnreadableError
    moment = now.timestamp()
    # A sign of life older than this attempt belongs to an earlier one.
    silent = moment - max(began, beat or began) > _SILENT_AFTER
    too_long = limit is not None and limit > 0 and moment - began > limit
    return None if silent or too_long else _NOT_RELEVANT


class HermesReader:
    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir

    def __call__(self, action: Action, target: str | None, now: datetime) -> str | None:
        """A refusal code, or `None` when the action makes sense now."""
        try:
            if action.requires == "gateway_may_run":
                return _gateway(self._data_dir)
            if target is None:
                return _UNREADABLE
            if action.requires == "task_running":
                return _task(self._data_dir, target, now)
            return _job(self._data_dir, target, action.requires == "job_not_running")
        except _UnreadableError:
            return _UNREADABLE
