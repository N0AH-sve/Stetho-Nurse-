"""What NURSE keeps and publishes: its private state and journal, and the results.

The state and the journal are in `log_dir`, which STETHO cannot reach: replays and limits rest on
them. The results directory is read by STETHO.
"""

import fcntl
import json
import os
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
LOG_ENTRIES = 100
LOG_INVALID_ENTRIES = 20  # a flood of invalid files must not hide the real actions
JOURNAL_BYTES = 1024 * 1024
JOURNAL_FILES = 5
_MAX_STATE_BYTES = 4 * 1024 * 1024
STATE_NAME = "state.json"
JOURNAL_NAME = "journal.jsonl"
# A request identifier cannot start with "_": no result can take this name.
LOG_NAME = "_log.json"
_TEMPORARY = ".tmp-"
LOCK_NAME = "lock"

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
    recent: list[Entry] = field(default_factory=list)  # the most recent first

    def forget(self, now: int) -> None:
        self.handled = {k: at for k, at in self.handled.items() if now - at < HANDLED_SECONDS}
        # A launch dated in the future, after a change of the clock, is kept and counted.
        self.launches = [item for item in self.launches if now - item.at < LAUNCH_SECONDS]

    def remember(self, entry: Entry) -> None:
        kept: list[Entry] = []
        invalid = 0
        for item in (entry, *self.recent):
            if item["prescription"] is None:
                invalid += 1
                if invalid > LOG_INVALID_ENTRIES:
                    continue
            kept.append(item)
        self.recent = kept[:LOG_ENTRIES]

    def copy(self) -> "State":
        return State(dict(self.handled), list(self.launches), list(self.recent))


def _is_time(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _parse_state(document: object) -> State:
    """Anything that is not exactly what NURSE writes raises `StateError`."""
    if not isinstance(document, dict) or set(document) != {"handled", "launches", "recent"}:
        raise StateError
    handled, launches, recent = (document[key] for key in ("handled", "launches", "recent"))
    if not isinstance(handled, dict) or not all(_is_time(at) for at in handled.values()):
        raise StateError
    if not isinstance(recent, list) or not all(
        isinstance(item, dict) and set(item) == {"prescription", "result"} for item in recent
    ):
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
    return State(dict(handled), parsed, list(recent))


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
        document = json.loads(content)
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
    }
    _write_atomically(log_dir / STATE_NAME, document, 0o600)


def append_journal(log_dir: Path, entry: Entry) -> None:
    """The private journal: one line per handled request, in a few files of limited size."""
    line = json.dumps(entry, ensure_ascii=True, allow_nan=False) + "\n"
    journal = log_dir / JOURNAL_NAME
    try:
        size = journal.stat().st_size
    except FileNotFoundError:
        size = 0
    if size and size + len(line) > JOURNAL_BYTES:
        for number in range(JOURNAL_FILES - 1, 0, -1):
            older = log_dir / (f"{JOURNAL_NAME}.{number - 1}" if number > 1 else JOURNAL_NAME)
            if older.exists():
                older.replace(log_dir / f"{JOURNAL_NAME}.{number}")
    descriptor = os.open(journal, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
        stream.write(line)


def result_path(results_dir: Path, rx_id: str) -> Path:
    """`rx_id` must already be checked: it becomes a file name."""
    return results_dir / f"{rx_id}.json"


def write_result(results_dir: Path, result: Entry) -> None:
    # Readable by the group that STETHO shares with NURSE.
    _write_atomically(result_path(results_dir, result["rx_id"]), result, 0o640)


def write_log(results_dir: Path, entries: Iterable[Entry]) -> None:
    _write_atomically(results_dir / LOG_NAME, list(entries), 0o640)


def prune_results(results_dir: Path, now: float) -> None:
    """Removes old results, and the oldest ones above the limit."""
    found = []
    for path in results_dir.iterdir():
        if path.name == LOG_NAME or path.suffix != ".json" or path.name.startswith("."):
            continue
        try:
            found.append((path.lstat().st_mtime, path.name, path))
        except FileNotFoundError:
            continue
    found.sort(reverse=True)
    for index, (written, _, path) in enumerate(found):
        if index >= MAX_RESULTS or now - written > RESULT_SECONDS:
            path.unlink(missing_ok=True)
