"""Receives the requests and checks each one again. No command is run yet.

The prescription folder is written by STETHO, the exposed service: everything in it is hostile.
"""

import heapq
import json
import logging
import os
import re
import stat
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from nurse.records import (
    MAX_HANDLED,
    RX_ID,
    Entry,
    State,
    StateError,
    append_journal,
    create_state_files,
    load_started,
    load_state,
    prune_results,
    recover_actions,
    result_path,
    save_state,
    write_log,
    write_result,
)
from nurse.settings import ACTION_CODE, ACTION_TARGET, Action, Settings

_LOG = logging.getLogger("nurse")
# The same patterns as `Prescription` in the API.
_REQUESTER = re.compile(r"[^\x00-\x1f\x7f-\x9f\ud800-\udfff]{1,64}")
_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,9})?"
    r"(Z|[+-]([01][0-9]|2[0-3]):[0-5][0-9])"
)
_FIELDS = {"rx_id", "action", "target", "origin", "requested_by", "created_at"}
_SUFFIX = ".json"
MAX_REQUEST_BYTES = 4096
MAX_PER_PASS = 20
MAX_STUCK = 1000
_PRUNE_EVERY = timedelta(minutes=1)
MAX_AGE = timedelta(minutes=5)
MAX_ADVANCE = timedelta(minutes=1)
RESTART_SPACING_SECONDS = 120  # between two restarts, of either kind and any origin
RESTART_VERIFICATION = "gateway_restarted"


@dataclass(frozen=True)
class Prescription:
    rx_id: str
    action: str
    target: str | None
    origin: str
    requested_by: str
    created_at: str

    def moment(self) -> datetime:
        return datetime.fromisoformat(self.created_at)


# Says why Hermes does not allow the action now, as a refusal code, or `None`.
HermesCheck = Callable[[Action, str | None, datetime], str | None]


def refuse_unchecked(_action: Action, _target: str | None, _now: datetime) -> str | None:
    """Without a reader of Hermes, nothing can be checked there."""
    return "state_unreadable"


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document = dict(pairs)
    if len(document) != len(pairs):
        raise ValueError("repeated key")
    return document


def _no_constant(_value: str) -> object:
    raise ValueError("not a JSON number")


def _matches(pattern: re.Pattern[str], value: object) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def parse_prescription(content: bytes, rx_id: str) -> Prescription | None:
    """`None` for anything that is not exactly a prescription named after its file."""
    if len(content) > MAX_REQUEST_BYTES:
        return None
    try:
        document = json.loads(
            content.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_no_constant
        )
    except (ValueError, RecursionError):
        return None
    if not isinstance(document, dict) or set(document) != _FIELDS:
        return None
    target = document["target"]
    if (
        document["rx_id"] != rx_id
        or not _matches(ACTION_CODE, document["action"])
        or not (target is None or _matches(ACTION_TARGET, target))
        or document["origin"] not in ("manual", "auto")
        or not _matches(_REQUESTER, document["requested_by"])
        or not _matches(_TIMESTAMP, document["created_at"])
    ):
        return None
    prescription = Prescription(**document)
    try:
        prescription.moment()
    except ValueError:
        return None
    return prescription


def read_request(folder: int, name: str) -> bytes | None:
    """The content of a regular file without link, or `None`. Reads one byte above the limit."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOCTTY | os.O_CLOEXEC
    try:
        descriptor = os.open(name, flags, dir_fd=folder)
    except OSError:
        return None
    try:
        found = os.fstat(descriptor)
        if not stat.S_ISREG(found.st_mode) or found.st_nlink != 1:
            return None
        return os.read(descriptor, MAX_REQUEST_BYTES + 1)
    except OSError:
        return None
    finally:
        os.close(descriptor)


def limit_reached(state: State, action: Action, origin: str, maximum: int, now: int) -> str | None:
    """A refusal code. `state` must have forgotten what is older than an hour."""
    if action.verify == RESTART_VERIFICATION and any(
        item.restart and now - item.at < RESTART_SPACING_SECONDS for item in state.launches
    ):
        return "too_soon"
    per_action = action.auto_per_hour if origin == "auto" else action.manual_per_hour
    same = sum(i.action == action.code and i.origin == origin for i in state.launches)
    if len(state.launches) >= maximum or same >= per_action:
        return "rate_limited"
    return None


class Reception:
    def __init__(
        self,
        settings: Settings,
        actions: Mapping[str, Action],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        check_hermes: HermesCheck = refuse_unchecked,
    ) -> None:
        self._settings = settings
        self._actions = actions
        self._clock = clock
        self._check_hermes = check_hermes
        self._stuck: set[str] = set()  # files that could not be handled: not tried every second
        self._pruned_at: datetime | None = None
        self._full_of_stuck = False
        self._state_failed = False  # said once, not every second

    def recover(self) -> None:
        """To call before the first request: answers what an interruption left unanswered.

        Raises `StateError` for an unusable state: what was interrupted is then unknown.
        """
        state = load_state(self._settings.log_dir)
        create_state_files(self._settings.log_dir, state)
        directories = self._settings.log_dir, self._settings.results_dir
        now = self._clock()
        for rx_id in recover_actions(*directories, state, _timestamp(now), int(now.timestamp())):
            _LOG.warning("action %s was interrupted: its effect is not verified", rx_id)
        # An interruption may have come just before the public log. Nothing rests on it: a
        # failure here does not stop NURSE, and the next request writes it again.
        try:
            write_log(self._settings.results_dir, state.log())
        except OSError as error:
            _LOG.error("the public log cannot be written: %s", type(error).__name__)

    def run_once(self) -> int:
        """Handles a few waiting requests and returns their number."""
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
        folder = os.open(self._settings.prescriptions_dir, flags)
        try:
            names = self._waiting(folder)
            if names:
                state = self._load()
                # Without a state, the identifiers of the actions are read apart.
                actions = self._load_actions() if state is None else frozenset()
                for name in names:
                    self._handle(folder, name, state, actions)
        finally:
            os.close(folder)
        self._prune(bool(names))
        return len(names)

    def _load(self) -> State | None:
        try:
            state = load_state(self._settings.log_dir, running=True)
        except StateError:
            if not self._state_failed:
                _LOG.error("the state of NURSE cannot be read: every request is refused")
            self._state_failed = True
            return None
        self._state_failed = False
        return state

    def _load_actions(self) -> frozenset[str] | None:
        """`None` when the actions cannot be known."""
        try:
            return load_started(self._settings.log_dir)
        except StateError:
            return None

    def _prune(self, handled: bool) -> None:
        """Old results go even when no request arrives."""
        now = self._clock()
        if handled or self._pruned_at is None or abs(now - self._pruned_at) >= _PRUNE_EVERY:
            state = self._load()
            started = None if state is None else state.known_actions()
            prune_results(self._settings.results_dir, now.timestamp(), started)
            self._pruned_at = now

    def _waiting(self, folder: int) -> list[str]:
        """The first names `<rx_id>.json` that are not folders. Anything else is left alone.

        STETHO may fill the folder: the names are never all held in memory.
        """
        still_stuck: set[str] = set()

        def candidates() -> Iterator[str]:
            with os.scandir(folder) as entries:
                for entry in entries:
                    name = entry.name
                    if (
                        name.endswith(_SUFFIX)
                        and RX_ID.fullmatch(name.removesuffix(_SUFFIX))
                        and not entry.is_dir(follow_symlinks=False)
                    ):
                        if name in self._stuck:
                            still_stuck.add(name)
                        else:
                            yield name

        found = heapq.nsmallest(MAX_PER_PASS, candidates())
        self._stuck = still_stuck
        # With too many files that cannot be handled, nothing more is tried.
        full = len(still_stuck) >= MAX_STUCK
        if full and not self._full_of_stuck:
            _LOG.error("too many requests cannot be handled: nothing more is tried")
        self._full_of_stuck = full
        return [] if full else found

    def _handle(
        self, folder: int, name: str, state: State | None, actions: frozenset[str] | None
    ) -> None:
        rx_id = name.removesuffix(_SUFFIX)
        content = read_request(folder, name)
        prescription = None if content is None else parse_prescription(content, rx_id)
        now = self._clock()
        try:
            self._record(rx_id, prescription, state, actions, now)
            os.unlink(name, dir_fd=folder)
        except OSError as error:
            # The message could hold a path of the installation.
            _LOG.error("a request could not be handled: %s", type(error).__name__)
            self._stuck.add(name)

    def _record(
        self,
        rx_id: str,
        prescription: Prescription | None,
        state: State | None,
        actions: frozenset[str] | None,
        now: datetime,
    ) -> None:
        seconds = int(now.timestamp())
        answered = result_path(self._settings.results_dir, rx_id).exists()
        if state is not None:
            actions = state.known_actions()
            answered = answered or rx_id in actions
        # An action only has its own answer. When the actions cannot be known, nothing is written.
        result_kept = answered or actions is None or rx_id in actions
        if state is not None:
            state.forget(seconds)
            if len(state.handled) >= MAX_HANDLED:
                state = None  # nothing more could be remembered
        if state is None:
            reason = "state_unreadable"
        elif answered or rx_id in state.handled:
            reason = "duplicate"
        elif prescription is None:
            reason = "invalid_request"
        else:
            reason = self._refusal(prescription, state, now)
        entry: Entry = {
            "prescription": None if prescription is None else asdict(prescription),
            "result": _refused(rx_id, reason, now),
        }
        if state is not None:
            # Saved first: nothing may be answered, or later run, without being remembered.
            saved = state.copy()
            saved.handled[rx_id] = seconds
            saved.remember(entry)
            save_state(self._settings.log_dir, saved)
            # Changed only once saved: a failure must leave no trace for the next request.
            state.handled, state.recent = saved.handled, saved.recent
        append_journal(self._settings.log_dir, entry)
        # The first answer to an identifier is never replaced.
        if not result_kept:
            write_result(self._settings.results_dir, entry["result"])
        if state is not None:
            write_log(self._settings.results_dir, state.log())
        _LOG.info("request %s refused: %s", rx_id, reason)

    def _refusal(self, request: Prescription, state: State, now: datetime) -> str:
        """The checks, in order. Nothing is run yet: a valid request is refused too."""
        age = now - request.moment()
        if age > MAX_AGE or -age > MAX_ADVANCE:
            return "expired"
        action = self._actions.get(request.action)
        if action is None:
            return "unknown_action"
        if request.origin == "auto":
            return "auto_disabled"  # automatic actions come after the alerts
        if action.target is None:
            if request.target is not None:
                return "invalid_target"
        elif request.target is None or not action.target.allows(request.target):
            return "invalid_target"
        reason = self._check_hermes(action, request.target, now)
        if reason is None:
            maximum = self._settings.max_actions_per_hour
            reason = limit_reached(state, action, request.origin, maximum, int(now.timestamp()))
        return reason or "execution_disabled"


def _timestamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _refused(rx_id: str, reason: str, now: datetime) -> Entry:
    return {
        "rx_id": rx_id,
        "status": "refused",
        "refusal_reason": reason,
        "exit_code": None,
        "output_excerpt": None,
        "anomaly": None,
        "started_at": None,
        "finished_at": _timestamp(now),
        "verified": None,
    }
