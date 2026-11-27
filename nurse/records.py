"""What NURSE keeps and publishes: its private state and journal, and the results.

The state and the journal are in `log_dir`, which STETHO cannot reach: replays and limits rest on
them. The results directory is read by STETHO.
"""

import fcntl
import json
import math
import os
import re
import tempfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

# Longer than the time during which a request can be accepted.
HANDLED_SECONDS = int(timedelta(minutes=10).total_seconds())
LAUNCH_SECONDS = int(timedelta(hours=1).total_seconds())
RESULT_SECONDS = int(timedelta(days=7).total_seconds())
MAX_RESULTS = 1000  # a flood of requests must not fill the disk
# Above what the accepted rate can reach in 10 minutes. It keeps the state loadable.
MAX_HANDLED = 20_000
# The public log shows the started actions and, apart from them so that a flood of refused
# requests cannot hide any, the last refusals.
LOG_ACTIONS = 80
LOG_REFUSALS = 20
JOURNAL_BYTES = 1024 * 1024
JOURNAL_FILES = 5
_MAX_STATE_BYTES = 4 * 1024 * 1024
_MAX_RESULT_BYTES = 64 * 1024
STATE_NAME = "state.json"
JOURNAL_NAME = "journal.jsonl"  # the refused requests
ACTIONS_NAME = "actions.jsonl"  # the started actions: refusals never reach this journal
# A request identifier cannot start with "_": no result can take this name.
LOG_NAME = "_log.json"
_TEMPORARY = ".tmp-"
LOCK_NAME = "lock"

# The statuses that end a started action, and the fields of a result, as in the API.
ENDED = frozenset({"done", "failed", "timeout", "unexpected", "unverified"})
_RESULT_FIELDS = frozenset(
    {
        "rx_id",
        "status",
        "refusal_reason",
        "exit_code",
        "output_excerpt",
        "anomaly",
        "started_at",
        "finished_at",
        "verified",
    }
)

# The same pattern as `PrescriptionId` in the API. An identifier becomes a file name.
RX_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_:.-]{0,63}")

Entry = dict[str, Any]  # `{"prescription": ... | None, "result": ...}`


class StateError(Exception):
    """The state cannot be trusted: everything must be refused."""


@dataclass(frozen=True)
class Launch:
    """An action that NURSE started: what the limits count."""

    at: int
    action: str
    origin: str
    restart: bool


@dataclass
class State:
    handled: dict[str, int] = field(default_factory=dict)  # rx_id to the time it was handled
    launches: list[Launch] = field(default_factory=list)
    recent: list[Entry] = field(default_factory=list)  # refusals, the most recent first
    actions: list[Entry] = field(default_factory=list)  # ended actions, the most recent first
    pending: dict[str, Entry] = field(default_factory=dict)  # started and not yet answered

    def forget(self, now: int) -> None:
        self.handled = {k: at for k, at in self.handled.items() if now - at < HANDLED_SECONDS}
        # A launch dated in the future, after a change of the clock, is kept and counted.
        self.launches = [item for item in self.launches if now - item.at < LAUNCH_SECONDS]

    def remember(self, entry: Entry) -> None:
        """Keeps a refused request."""
        self.recent = [entry, *self.recent][:LOG_REFUSALS]

    def log(self) -> list[Entry]:
        """The public log, the most recent first."""
        entries = [*self.actions, *self.recent]
        return sorted(entries, key=lambda e: str(e["result"].get("finished_at")), reverse=True)

    def copy(self) -> "State":
        return State(
            dict(self.handled),
            list(self.launches),
            list(self.recent),
            list(self.actions),
            dict(self.pending),
        )


def _is_time(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_identifier(value: object) -> bool:
    return isinstance(value, str) and RX_ID.fullmatch(value) is not None


def _is_entry(item: object) -> bool:
    if not isinstance(item, dict) or set(item) != {"prescription", "result"}:
        return False
    prescription, result = item["prescription"], item["result"]
    return (
        (prescription is None or isinstance(prescription, dict))
        and isinstance(result, dict)
        and _is_identifier(result.get("rx_id"))
        and isinstance(result.get("status"), str)
        and isinstance(result.get("finished_at"), str)
    )


def _are_entries(value: object) -> bool:
    return isinstance(value, list) and all(_is_entry(item) for item in value)


def _is_pending(rx_id: str, item: object) -> bool:
    if not _is_identifier(rx_id) or not isinstance(item, dict):
        return False
    if set(item) != {"prescription", "started_at"} or not isinstance(item["started_at"], str):
        return False
    prescription = item["prescription"]
    return isinstance(prescription, dict) and prescription.get("rx_id") == rx_id


def _no_constant(_value: str) -> object:
    raise ValueError("not a JSON number")


def _finite(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):  # too large a number would not be written back
        raise ValueError("not a JSON number")
    return number


def _parse_state(document: object) -> State:
    """Anything that is not exactly what NURSE writes raises `StateError`."""
    names = ("handled", "launches", "recent", "actions", "pending")
    if not isinstance(document, dict) or set(document) != set(names):
        raise StateError
    handled, launches, recent, actions, pending = (document[key] for key in names)
    if not isinstance(handled, dict) or not all(_is_time(at) for at in handled.values()):
        raise StateError
    if not _are_entries(recent) or not _are_entries(actions):
        raise StateError
    if not isinstance(pending, dict) or not all(_is_pending(k, v) for k, v in pending.items()):
        raise StateError
    if not isinstance(launches, list):
        raise StateError
    parsed = []
    for item in launches:
        if (
            not isinstance(item, dict)
            or set(item) != {"at", "action", "origin", "restart"}
            or not _is_time(item["at"])
            or not isinstance(item["action"], str)
            or item["origin"] not in ("manual", "auto")
            or not isinstance(item["restart"], bool)
        ):
            raise StateError
        parsed.append(Launch(**item))
    return State(dict(handled), parsed, list(recent), list(actions), dict(pending))


def load_state(log_dir: Path) -> State:
    """A missing state is a first start. Any other doubt raises `StateError`."""
    try:
        with (log_dir / STATE_NAME).open("rb") as stream:
            content = stream.read(_MAX_STATE_BYTES + 1)
    except FileNotFoundError:
        if log_dir.is_dir():
            return State()
        raise StateError from None
    except OSError:
        raise StateError from None
    if len(content) > _MAX_STATE_BYTES:
        raise StateError
    try:
        document = json.loads(content, parse_constant=_no_constant, parse_float=_finite)
    except (ValueError, RecursionError):
        raise StateError from None
    return _parse_state(document)


def _sync_folder(folder: Path) -> None:
    """Without this, a power cut could lose a rename, and with it what must not be replayed."""
    descriptor = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def lock_instance(log_dir: Path) -> int:
    """Only one NURSE may use a private folder. The lock lasts while the file stays open.

    Raises `OSError` when it is taken or cannot be taken.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW
    descriptor = os.open(log_dir / LOCK_NAME, flags, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(descriptor)
        raise
    return descriptor


def remove_temporary_files(folder: Path) -> None:
    """What a killed NURSE left half written. Only safe with the lock held."""
    for path in folder.glob(f"{_TEMPORARY}*"):
        path.unlink(missing_ok=True)


def _write_atomically(path: Path, value: object, mode: int) -> None:
    descriptor, name = tempfile.mkstemp(prefix=_TEMPORARY, dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), mode)
            json.dump(value, stream, ensure_ascii=True, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        _sync_folder(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def save_state(log_dir: Path, state: State) -> None:
    document = {
        "handled": state.handled,
        "launches": [asdict(item) for item in state.launches],
        "recent": state.recent,
        "actions": state.actions,
        "pending": state.pending,
    }
    _write_atomically(log_dir / STATE_NAME, document, 0o600)


def append_journal(
    log_dir: Path, entry: Entry, name: str = JOURNAL_NAME, durable: bool = False
) -> None:
    """A private journal: one line per entry, in a few files of limited size."""
    line = json.dumps(entry, ensure_ascii=True, allow_nan=False) + "\n"
    journal = log_dir / name
    try:
        size = journal.stat().st_size
    except FileNotFoundError:
        size = 0
    if size and size + len(line) > JOURNAL_BYTES:
        for number in range(JOURNAL_FILES - 1, 0, -1):
            older = log_dir / (f"{name}.{number - 1}" if number > 1 else name)
            if older.exists():
                older.replace(log_dir / f"{name}.{number}")
    descriptor = os.open(journal, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, "a+b") as stream:
        end = stream.seek(0, os.SEEK_END)
        # A line cut short, by a full disk, must not swallow the next one.
        if end and os.pread(stream.fileno(), 1, end - 1) != b"\n":
            stream.write(b"\n")
        stream.write(line.encode("ascii"))
    if durable:
        _sync_journal(log_dir, name)


def _sync_journal(log_dir: Path, name: str) -> None:
    descriptor = os.open(log_dir / name, os.O_RDONLY | os.O_CLOEXEC)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _sync_folder(log_dir)


def _is_last_line(log_dir: Path, name: str, entry: Entry) -> bool:
    """Whether the journal already ends with this very entry."""
    try:
        with (log_dir / name).open("rb") as stream:
            end = stream.seek(0, os.SEEK_END)
            stream.seek(max(0, end - JOURNAL_BYTES))
            lines = stream.read().splitlines()
    except FileNotFoundError:
        return False
    try:
        return bool(lines) and bool(json.loads(lines[-1]) == entry)
    except (ValueError, RecursionError):
        return False


def result_path(results_dir: Path, rx_id: str) -> Path:
    """`rx_id` must already be checked: it becomes a file name."""
    return results_dir / f"{rx_id}.json"


def write_result(results_dir: Path, result: Entry) -> None:
    # Readable by the group that STETHO shares with NURSE.
    _write_atomically(result_path(results_dir, result["rx_id"]), result, 0o640)


def write_log(results_dir: Path, entries: Iterable[Entry]) -> None:
    _write_atomically(results_dir / LOG_NAME, list(entries), 0o640)


def _read_result(path: Path) -> Entry | None:
    try:
        with path.open("rb") as stream:
            result = json.loads(stream.read(_MAX_RESULT_BYTES), parse_constant=_no_constant)
    except (FileNotFoundError, ValueError, RecursionError):
        return None
    return result if isinstance(result, dict) else None


def _is_refusal(path: Path) -> bool:
    try:
        result = _read_result(path)
    except OSError:
        return False
    return result is not None and result.get("status") == "refused"


def prune_results(
    results_dir: Path, now: float, actions: frozenset[str] | None = frozenset()
) -> None:
    """Removes old results, and the oldest ones above the limit.

    The results of the started `actions` do not count in the limit: refusals cannot push them out.
    When the actions are unknown (`None`), only the results that say they are refusals are
    removed, so that a flood cannot fill the disk.
    """
    found = []
    for path in results_dir.iterdir():
        if path.name == LOG_NAME or path.suffix != ".json" or path.name.startswith("."):
            continue
        if actions is None and not _is_refusal(path):
            continue
        try:
            found.append((path.lstat().st_mtime, path.name, path))
        except FileNotFoundError:
            continue
    found.sort(reverse=True)
    others = 0
    for written, _, path in found:
        started = actions is not None and path.stem in actions
        others += not started
        if now - written > RESULT_SECONDS or (not started and others > MAX_RESULTS):
            path.unlink(missing_ok=True)


def start_action(
    log_dir: Path, state: State, prescription: Entry, launch: Launch, started_at: str
) -> None:
    """Must succeed before anything is run: from then on, the request is never run again."""
    rx_id = prescription["rx_id"]
    saved = state.copy()
    saved.handled[rx_id] = launch.at
    saved.launches.append(launch)
    saved.pending[rx_id] = {"prescription": prescription, "started_at": started_at}
    save_state(log_dir, saved)
    state.handled, state.launches, state.pending = saved.handled, saved.launches, saved.pending


def finish_action(log_dir: Path, results_dir: Path, state: State, result: Entry) -> None:
    """Answers a started action. It stays pending until everything is written.

    The answer comes first: after an interruption, it is the one that is kept.
    """
    rx_id = result["rx_id"]
    entry = {"prescription": state.pending[rx_id]["prescription"], "result": result}
    write_result(results_dir, result)
    # Not twice when a later step failed and NURSE ends the same action again.
    if _is_last_line(log_dir, ACTIONS_NAME, entry):
        _sync_journal(log_dir, ACTIONS_NAME)  # the sync may be what had failed
    else:
        append_journal(log_dir, entry, ACTIONS_NAME, durable=True)
    saved = state.copy()
    del saved.pending[rx_id]
    saved.actions = [entry, *saved.actions][:LOG_ACTIONS]
    save_state(log_dir, saved)
    state.pending, state.actions = saved.pending, saved.actions
    write_log(results_dir, state.log())


def _written_answer(results_dir: Path, rx_id: str) -> Entry | None:
    """The complete answer to a started action, if it was written before the interruption."""
    result = _read_result(result_path(results_dir, rx_id))
    if (
        result is None
        or set(result) != _RESULT_FIELDS
        or result["rx_id"] != rx_id
        or not isinstance(result["status"], str)
        or result["status"] not in ENDED
        or not isinstance(result["finished_at"], str)
    ):
        return None
    return result


def recover_actions(log_dir: Path, results_dir: Path, state: State, now: str) -> list[str]:
    """Ends what an interruption left unanswered, without ever running it again.

    Whether such an action had an effect is unknown: it is answered `unverified`.
    """
    recovered = list(state.pending)
    for rx_id in recovered:
        result = _written_answer(results_dir, rx_id) or {
            "rx_id": rx_id,
            "status": "unverified",
            "refusal_reason": None,
            "exit_code": None,
            "output_excerpt": None,
            "anomaly": "action_unverified",
            "started_at": state.pending[rx_id]["started_at"],
            "finished_at": now,
            "verified": False,
        }
        finish_action(log_dir, results_dir, state, result)
    return recovered
