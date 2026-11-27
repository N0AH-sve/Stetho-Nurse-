"""Reads the platform entries of the gateway state file. Free text never leaves this module."""

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from stetho.anomalies import Anomaly, AnomalyCode, received_value
from stetho.modules.gateway.reader import Adapter as GatewayAdapter
from stetho.modules.gateway.reader import GatewayState, read_state_file
from stetho.patterns import PROFILE_NAME
from stetho.sources import Sleep
from stetho.yamlfile import load_yaml

MODULE = "platforms"
_ADAPTER_FILE = Path(__file__).parent / "adapters" / "hermes-0.21.yaml"
_PLATFORM_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_FUTURE_TOLERANCE = timedelta(minutes=5)
_OLDEST_DATE = datetime(2020, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class Adapter:
    known_fields: tuple[str, ...]
    platform_states: tuple[str, ...]


def load_adapter(path: Path = _ADAPTER_FILE) -> Adapter:
    document = load_yaml(path)
    names = Adapter.__dataclass_fields__.keys()
    if not isinstance(document, dict) or document.keys() != names:
        raise ValueError("invalid platforms adapter: unexpected keys")
    values: dict[str, tuple[str, ...]] = {}
    for name in names:
        items = document[name]
        if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
            raise ValueError(f"invalid platforms adapter: {name} must be a list of texts")
        values[name] = tuple(items)
    return Adapter(**values)


@dataclass(frozen=True)
class Adapters:
    """The platform entries are in the state file of the gateway: both formats are needed."""

    platforms: Adapter
    gateway: GatewayAdapter


@dataclass(frozen=True, kw_only=True)
class PlatformEntry:
    key: str  # `name`, or `profile:name` when one gateway serves several profiles
    served_profile: str | None
    name: str
    state: str | None  # None: a value the adapter does not know
    needs_attention: bool
    retrying_since: datetime | None
    current: bool  # written by the gateway process that wrote the file
    has_error_text: bool  # the text itself is never kept


@dataclass(frozen=True)
class PlatformsReading:
    profile: str
    gateway: GatewayState | None  # to tell whether the gateway is alive
    entries: tuple[PlatformEntry, ...]
    complete: bool  # False: the file, or part of its platforms, could not be used
    anomalies: tuple[Anomaly, ...]  # those of this module
    # Those of the rest of the file, for when no `gateway` module reports them.
    file_anomalies: tuple[Anomaly, ...] = ()
    # Identifies the gateway process that wrote the file.
    process: tuple[object, object] = (None, None)


def _process(document: Mapping[str, object]) -> tuple[object, object]:
    pid, start = document.get("pid"), document.get("start_time")
    # Only numbers: anything else could not be compared, nor kept in memory.
    return (
        pid if isinstance(pid, int) else None,
        start if isinstance(start, int) else None,
    )


class _Parser:
    def __init__(self, source: str, adapter: Adapter, now: datetime) -> None:
        self.source = source
        self.adapter = adapter
        self.now = now
        self.anomalies: list[Anomaly] = []
        self.complete = True

    def _add(self, code: AnomalyCode, **fields: object) -> None:
        self.anomalies.append(
            Anomaly(code=code, module=MODULE, source=self.source, **fields)  # type: ignore[arg-type]
        )

    def _unusable(self, field: str) -> None:
        self._add(AnomalyCode.SCHEMA_CHANGED, field=field)
        self.complete = False

    def entries(self, document: Mapping[str, object]) -> tuple[PlatformEntry, ...]:
        platforms = document.get("platforms")
        if not isinstance(platforms, dict):
            self._unusable("platforms")
            return ()
        # Compared as written: turning a value into another could make two writers look alike.
        process = (document.get("pid"), document.get("start_time"))
        found = []
        for key in sorted(platforms, key=str):
            entry = self._entry(key, platforms[key], process)
            if entry is not None:
                found.append(entry)
        return tuple(found)

    def _entry(
        self, key: str, value: object, process: tuple[object, object]
    ) -> PlatformEntry | None:
        profile, colon, name = key.rpartition(":")
        if not _PLATFORM_NAME.fullmatch(name) or (colon and not PROFILE_NAME.fullmatch(profile)):
            self._unusable("platforms.<name>")
            return None
        if not isinstance(value, dict) or "state" not in value:
            self._unusable("platforms.state")
            return None
        for field in sorted(set(value) - set(self.adapter.known_fields), key=str):
            safe = received_value(field)
            shown = safe if isinstance(safe, str) else "<hidden>"
            self._add(AnomalyCode.SCHEMA_CHANGED, field=f"platforms.{shown}")
        writer = (value.get("writer_pid"), value.get("writer_start_time"))
        return PlatformEntry(
            key=key,
            served_profile=profile or None,
            name=name,
            state=self._state(value["state"]),
            needs_attention=self._attention(value),
            retrying_since=self._since(value),
            # Without a process number, nothing tells who wrote the entry.
            current=isinstance(process[0], int) and writer == process,
            has_error_text=bool(value.get("error_message")),
        )

    def _state(self, value: object) -> str | None:
        if isinstance(value, str) and value in self.adapter.platform_states:
            return value
        self._add(
            AnomalyCode.UNEXPECTED_VALUE,
            field="platforms.state",
            received=received_value(value),
            expected=self.adapter.platform_states,
        )
        return None

    def _attention(self, value: Mapping[str, object]) -> bool:
        attention = value.get("needs_attention", False)
        if isinstance(attention, bool):
            return attention
        # Whether the platform asks for something is unknown: the card cannot be healthy.
        self._unusable("platforms.needs_attention")
        return False

    def _since(self, value: Mapping[str, object]) -> datetime | None:
        raw = value.get("retrying_since")
        if raw is None:
            return None
        date = None
        if isinstance(raw, str):
            try:
                date = datetime.fromisoformat(raw)
            except ValueError:
                date = None
        if date is None or date.utcoffset() is None:
            self._add(AnomalyCode.SCHEMA_CHANGED, field="platforms.retrying_since")
            return None
        if not _OLDEST_DATE <= date <= self.now + _FUTURE_TOLERANCE:
            self._add(
                AnomalyCode.IMPLAUSIBLE_VALUE,
                field="platforms.retrying_since",
                received=received_value(raw),
            )
            return None
        return date


def read_platforms(
    data_dir: Path, profile: str, *, adapters: Adapters, now: datetime, sleep: Sleep
) -> PlatformsReading:
    """Read the platforms of one profile. `now` must have a time zone."""
    read = read_state_file(data_dir, profile, adapter=adapters.gateway, now=now, sleep=sleep)
    file_anomalies = tuple(replace(item, module=MODULE) for item in read.anomalies)
    if read.document is None:
        return PlatformsReading(profile, None, (), False, (), file_anomalies)
    parser = _Parser(read.source, adapters.platforms, now)
    entries = parser.entries(read.document)
    return PlatformsReading(
        profile,
        read.state,
        entries,
        parser.complete,
        tuple(parser.anomalies),
        file_anomalies,
        _process(read.document),
    )
