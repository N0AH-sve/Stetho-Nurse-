"""Reads the gateway files of Hermes. Only this module knows their format.

Free text from Hermes never leaves this module: only known, filtered or typed values do.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from stetho.anomalies import HIDDEN, Anomaly, AnomalyCode, UnreadableReason, received_value
from stetho.config import HermesSettings
from stetho.patterns import PROFILE_NAME
from stetho.sources import InvalidContentError, Sleep, SourceFailure, read_source
from stetho.yamlfile import load_yaml

MODULE = "gateway"
STATE_FILE = "gateway_state.json"
EXIT_LOG = "logs/gateway-exit-diag.log"
ROOT_PROFILE = "default"
_PROFILES_DIR = "profiles"
_UNCLEAN_TAG = "gateway.previous_unclean_exit"
_ADAPTER_FILE = Path(__file__).parent / "adapters" / "hermes-0.21.yaml"
_MAX_STATE_BYTES = 1024 * 1024
_MAX_LOG_BYTES = 256 * 1024
_FUTURE_TOLERANCE = timedelta(minutes=5)
_OLDEST_DATE = datetime(2020, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class Adapter:
    required_fields: tuple[str, ...]
    known_fields: tuple[str, ...]
    gateway_states: tuple[str, ...]
    desired_states: tuple[str, ...]
    known_exit_reasons: tuple[str, ...]


def load_adapter(path: Path = _ADAPTER_FILE) -> Adapter:
    document = load_yaml(path)
    names = Adapter.__dataclass_fields__.keys()
    if not isinstance(document, dict) or document.keys() != names:
        raise ValueError("invalid gateway adapter: unexpected keys")
    values: dict[str, tuple[str, ...]] = {}
    for name in names:
        items = document[name]
        if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
            raise ValueError(f"invalid gateway adapter: {name} must be a list of texts")
        values[name] = tuple(items)
    return Adapter(**values)


@dataclass(frozen=True)
class Heartbeat:
    at: datetime
    # `gateway start`, `stop` and `restart` also write the date, even if the gateway is dead.
    written_by_command: bool


@dataclass(frozen=True, kw_only=True)
class GatewayState:
    state: str | None  # None: a value the adapter does not know
    heartbeat: Heartbeat | None  # None: no usable date
    exit_reason: str | None  # a known one only
    has_other_exit_reason: bool
    desired_state: str | None
    desired_state_unrecognised: bool  # present, but not to be relied upon
    active_agents: int | None
    served_profiles: tuple[str, ...]
    hermes_version: str | None


@dataclass(frozen=True)
class UncleanStop:
    detected_at: datetime  # when the next start noticed it
    suspected_oom: bool


@dataclass(frozen=True)
class GatewayReading:
    profile: str
    state: GatewayState | None  # None: `anomalies` says why
    last_unclean_stop: UncleanStop | None
    anomalies: tuple[Anomaly, ...]


def _failure(failure: SourceFailure, source: str) -> Anomaly:
    return Anomaly(code=failure.code, module=MODULE, source=source, reason=failure.reason)


def _field_name(name: str) -> str:
    # A key read from Hermes: filtered like any outside value.
    safe = received_value(name)
    return "<hidden>" if safe is HIDDEN else str(safe)


def _parse_object(content: bytes) -> dict[str, object]:
    try:
        document = json.loads(content.decode("utf-8"))
    except (ValueError, RecursionError):
        raise InvalidContentError from None
    if not isinstance(document, dict):
        raise InvalidContentError
    return document


def _date_from_text(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        date = datetime.fromisoformat(value)
    except ValueError:
        return None
    return date if date.utcoffset() is not None else None


def _plausible(date: datetime, now: datetime) -> bool:
    return _OLDEST_DATE <= date <= now + _FUTURE_TOLERANCE


class _StateParser:
    """Turns the content of the state file into neutral data, collecting anomalies."""

    def __init__(
        self, document: Mapping[str, object], source: str, adapter: Adapter, now: datetime
    ) -> None:
        self.document = document
        self.source = source
        self.adapter = adapter
        self.now = now
        self.anomalies: list[Anomaly] = []

    def _changed(self, field: str) -> None:
        self.anomalies.append(
            Anomaly(code=AnomalyCode.SCHEMA_CHANGED, module=MODULE, source=self.source, field=field)
        )

    def _unexpected(self, field: str, value: object, expected: tuple[str, ...]) -> None:
        self.anomalies.append(
            Anomaly(
                code=AnomalyCode.UNEXPECTED_VALUE,
                module=MODULE,
                source=self.source,
                field=field,
                received=received_value(value),
                expected=expected,
            )
        )

    def _implausible(self, field: str, value: object) -> None:
        self.anomalies.append(
            Anomaly(
                code=AnomalyCode.IMPLAUSIBLE_VALUE,
                module=MODULE,
                source=self.source,
                field=field,
                received=received_value(value),
            )
        )

    def parse(self) -> GatewayState | None:
        for name in sorted(self.document.keys() - set(self.adapter.known_fields)):
            self._changed(_field_name(name))
        missing = [name for name in self.adapter.required_fields if name not in self.document]
        for name in missing:
            self._changed(name)
        if missing:
            return None
        reason, has_other_reason = self._exit_reason()
        desired, desired_unrecognised = self._desired_state()
        return GatewayState(
            state=self._state(),
            heartbeat=self._heartbeat(),
            exit_reason=reason,
            has_other_exit_reason=has_other_reason,
            desired_state=desired,
            desired_state_unrecognised=desired_unrecognised,
            active_agents=self._active_agents(),
            served_profiles=self._served_profiles(),
            hermes_version=self._version(),
        )

    def _state(self) -> str | None:
        value = self.document["gateway_state"]
        if isinstance(value, str) and value in self.adapter.gateway_states:
            return value
        self._unexpected("gateway_state", value, self.adapter.gateway_states)
        return None

    def _heartbeat(self) -> Heartbeat | None:
        value = self.document["updated_at"]
        by_command = False
        date: datetime | None
        if isinstance(value, int) and not isinstance(value, bool):
            by_command = True
            try:
                date = datetime.fromtimestamp(value, UTC)
            except (OverflowError, ValueError, OSError):
                self._implausible("updated_at", value)
                return None
        else:
            date = _date_from_text(value)
            if date is None:
                self._changed("updated_at")
                return None
        if not _plausible(date, self.now):
            self._implausible("updated_at", value)
            return None
        return Heartbeat(date, by_command)

    def _exit_reason(self) -> tuple[str | None, bool]:
        value = self.document.get("exit_reason")
        if value is None or value == "":
            return None, False
        if isinstance(value, str) and value in self.adapter.known_exit_reasons:
            return value, False
        if not isinstance(value, str):
            self._changed("exit_reason")
        return None, True

    def _desired_state(self) -> tuple[str | None, bool]:
        if "desired_state" not in self.document:
            return None, False
        value = self.document["desired_state"]
        if isinstance(value, str) and value in self.adapter.desired_states:
            return value, False
        self._unexpected("desired_state", value, self.adapter.desired_states)
        return None, True

    def _active_agents(self) -> int | None:
        if "active_agents" not in self.document:
            return None
        value = self.document["active_agents"]
        if not isinstance(value, int) or isinstance(value, bool):
            self._changed("active_agents")
            return None
        if value < 0:
            self._implausible("active_agents", value)
            return None
        return value

    def _served_profiles(self) -> tuple[str, ...]:
        value = self.document.get("served_profiles")
        if value is None:
            return ()
        if not isinstance(value, list) or not all(
            isinstance(name, str) and PROFILE_NAME.fullmatch(name) for name in value
        ):
            self._changed("served_profiles")
            return ()
        return tuple(value)

    def _version(self) -> str | None:
        value = self.document.get("code_version")
        safe = received_value(value)
        return safe if isinstance(value, str) and isinstance(safe, str) else None


def _parse_log(content: bytes) -> tuple[Mapping[str, object] | None, bool]:
    """The last unclean stop event, and whether a complete line could not be read."""
    # What follows the last newline is a line still being written.
    lines = content.split(b"\n")[:-1]
    latest: Mapping[str, object] | None = None
    broken = False
    for line in lines:
        if not line.strip():
            continue
        try:
            event = _parse_object(line)
        except InvalidContentError:
            broken = True
            continue
        if event.get("tag") == _UNCLEAN_TAG:
            latest = event
    return latest, broken


def _read_unclean_stop(
    home: Path, source: str, now: datetime, sleep: Sleep
) -> tuple[UncleanStop | None, list[Anomaly]]:
    result = read_source(
        home / EXIT_LOG, _parse_log, max_bytes=_MAX_LOG_BYTES, sleep=sleep, tail=True
    )
    if isinstance(result, SourceFailure):
        # A history that was never written is not a fault.
        if result.code is AnomalyCode.SOURCE_MISSING:
            return None, []
        return None, [_failure(result, source)]
    event, broken = result
    anomalies = []
    if broken:
        anomalies.append(Anomaly(code=AnomalyCode.PARSE_ERROR, module=MODULE, source=source))
    if event is None:
        return None, anomalies
    date = _date_from_text(event.get("ts"))
    if date is None:
        anomalies.append(
            Anomaly(code=AnomalyCode.SCHEMA_CHANGED, module=MODULE, source=source, field="ts")
        )
        return None, anomalies
    if not _plausible(date, now):
        anomalies.append(
            Anomaly(
                code=AnomalyCode.IMPLAUSIBLE_VALUE,
                module=MODULE,
                source=source,
                field="ts",
                received=received_value(event.get("ts")),
            )
        )
        return None, anomalies
    return UncleanStop(date, event.get("suspected_oom") is True), anomalies


def read_gateway(
    data_dir: Path, profile: str, *, adapter: Adapter, now: datetime, sleep: Sleep
) -> GatewayReading:
    """Read the files of one profile. `now` must have a time zone."""
    prefix = "" if profile == ROOT_PROFILE else f"{_PROFILES_DIR}/{profile}/"
    home = data_dir / prefix
    source = prefix + STATE_FILE
    result = read_source(home / STATE_FILE, _parse_object, max_bytes=_MAX_STATE_BYTES, sleep=sleep)
    state: GatewayState | None = None
    if isinstance(result, SourceFailure):
        anomalies = [_failure(result, source)]
    else:
        parser = _StateParser(result, source, adapter, now)
        state = parser.parse()
        anomalies = parser.anomalies
    unclean_stop, log_anomalies = _read_unclean_stop(home, prefix + EXIT_LOG, now, sleep)
    return GatewayReading(profile, state, unclean_stop, (*anomalies, *log_anomalies))


def _has_state_file(home: Path) -> bool:
    try:
        (home / STATE_FILE).lstat()
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        return True  # read it, so that the problem is reported
    return True


def _discover_profiles(data_dir: Path) -> tuple[list[str], list[Anomaly]]:
    """Named profiles that have a gateway state file."""
    try:
        folders = sorted(path for path in (data_dir / _PROFILES_DIR).iterdir() if path.is_dir())
    except (FileNotFoundError, NotADirectoryError):
        return [], []
    except OSError as error:
        reason = (
            UnreadableReason.PERMISSION_DENIED
            if isinstance(error, PermissionError)
            else UnreadableReason.OTHER
        )
        failure = SourceFailure(AnomalyCode.SOURCE_UNREADABLE, reason)
        return [], [_failure(failure, _PROFILES_DIR)]
    names: list[str] = []
    anomalies: list[Anomaly] = []
    for folder in folders:
        if folder.name == ROOT_PROFILE or not _has_state_file(folder):
            continue
        if PROFILE_NAME.fullmatch(folder.name):
            names.append(folder.name)
        else:
            anomalies.append(
                Anomaly(
                    code=AnomalyCode.SCHEMA_CHANGED,
                    module=MODULE,
                    source=_PROFILES_DIR,
                    field="profile name",
                )
            )
    return names, anomalies


@dataclass(frozen=True)
class GatewaysReading:
    gateways: tuple[GatewayReading, ...]
    anomalies: tuple[Anomaly, ...]  # those that belong to no single profile


def read_gateways(
    hermes: HermesSettings, *, adapter: Adapter, now: datetime, sleep: Sleep
) -> GatewaysReading:
    """Read every gateway that is expected.

    With `profiles: auto`, the main profile is always expected, and another one if it has a
    state file. A listed profile is always expected, unless the main gateway serves it and it
    has no state file of its own.
    """

    def read(profile: str) -> GatewayReading:
        return read_gateway(hermes.data_dir, profile, adapter=adapter, now=now, sleep=sleep)

    root = read(ROOT_PROFILE)
    served = root.state.served_profiles if root.state else ()
    anomalies: list[Anomaly] = []
    if hermes.profiles == "auto":
        discovered, anomalies = _discover_profiles(hermes.data_dir)
        expected = [ROOT_PROFILE, *discovered]
    else:
        expected = [
            name
            for name in hermes.profiles
            if name == ROOT_PROFILE
            or name not in served
            or _has_state_file(hermes.data_dir / _PROFILES_DIR / name)
        ]
    gateways = tuple(root if name == ROOT_PROFILE else read(name) for name in expected)
    return GatewaysReading(gateways, tuple(anomalies))
