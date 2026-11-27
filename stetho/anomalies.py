"""Anomalies: everything that does not match what is expected.

An anomaly holds only what its text needs. Values read from Hermes are kept for two codes only,
and only if they are short and simple. Error messages never quote a value.
"""

import hashlib
import json
import re
from dataclasses import dataclass, fields, replace
from datetime import datetime
from enum import Enum, StrEnum
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Final

from stetho.errors import ErrorSummary
from stetho.i18n import Translator
from stetho.patterns import ACTION_CODE, ACTION_TARGET, SENSOR_ID

_SAFE_VALUE = re.compile(r"[A-Za-z0-9_.:+-]{1,64}")
# Larger integers are hidden anyway, and Python refuses to write huge ones.
_MAX_INT_BITS = 256


class AnomalyCode(StrEnum):
    SOURCE_MISSING = "source_missing"
    SOURCE_UNREADABLE = "source_unreadable"
    PARSE_ERROR = "parse_error"
    UNEXPECTED_VALUE = "unexpected_value"
    SCHEMA_CHANGED = "schema_changed"
    IMPLAUSIBLE_VALUE = "implausible_value"
    SENSOR_ERROR = "sensor_error"
    SENSOR_TIMEOUT = "sensor_timeout"
    ACTION_FAILED = "action_failed"
    ACTION_TIMEOUT = "action_timeout"
    ACTION_UNEXPECTED_OUTPUT = "action_unexpected_output"
    ACTION_UNVERIFIED = "action_unverified"
    PROXY_DENIED = "proxy_denied"


class UnreadableReason(StrEnum):
    PERMISSION_DENIED = "permission_denied"
    LOCKED = "locked"
    OTHER = "other"


class _Hidden(Enum):
    HIDDEN = "hidden"


# A value was received but is too long or not simple enough to be kept.
HIDDEN: Final = _Hidden.HIDDEN
type ReceivedValue = str | _Hidden


def received_value(raw: object) -> ReceivedValue:
    """Turn a value read from a source into what may be shown."""
    text: str | None = None
    if raw is None:
        text = "null"
    elif isinstance(raw, bool):
        text = "true" if raw else "false"
    elif isinstance(raw, int):
        text = str(raw) if raw.bit_length() <= _MAX_INT_BITS else None
    elif isinstance(raw, float):
        text = str(raw)
    elif isinstance(raw, str):
        text = raw
    return text if text is not None and _SAFE_VALUE.fullmatch(text) else HIDDEN


# For each code: the fields it needs, besides `code` and `module`.
_REQUIRED = MappingProxyType(
    {
        AnomalyCode.SOURCE_MISSING: ("source",),
        AnomalyCode.SOURCE_UNREADABLE: ("source", "reason"),
        AnomalyCode.PARSE_ERROR: ("source",),
        AnomalyCode.UNEXPECTED_VALUE: ("source", "field", "received", "expected"),
        AnomalyCode.SCHEMA_CHANGED: ("source", "field"),
        AnomalyCode.IMPLAUSIBLE_VALUE: ("source", "field", "received"),
        AnomalyCode.SENSOR_ERROR: ("error",),
        AnomalyCode.SENSOR_TIMEOUT: (),
        AnomalyCode.ACTION_FAILED: ("action", "exit_code"),
        AnomalyCode.ACTION_TIMEOUT: ("action", "timeout_seconds"),
        AnomalyCode.ACTION_UNEXPECTED_OUTPUT: ("action",),
        AnomalyCode.ACTION_UNVERIFIED: ("action",),
        AnomalyCode.PROXY_DENIED: ("action",),
    }
)


@dataclass(frozen=True, kw_only=True)
class Anomaly:
    """One anomaly seen during a reading. An invalid one raises `ValueError`."""

    code: AnomalyCode
    module: str
    source: str | None = None  # relative to the Hermes data directory
    field: str | None = None
    received: ReceivedValue | None = None  # build it with `received_value`
    expected: tuple[str, ...] = ()
    reason: UnreadableReason | None = None
    error: ErrorSummary | None = None
    action: str | None = None
    target: str | None = None  # optional: restarts have none
    exit_code: int | None = None
    timeout_seconds: int | None = None

    def __post_init__(self) -> None:
        if not SENSOR_ID.fullmatch(self.module):
            raise ValueError("invalid module")
        self._check_fields()
        if self.source is not None and (
            not self.source.strip() or PurePosixPath(self.source).is_absolute()
        ):
            raise ValueError("source must be a relative path")
        if self.field is not None and not self.field.strip():
            raise ValueError("field must not be empty")
        if isinstance(self.received, str) and not _SAFE_VALUE.fullmatch(self.received):
            raise ValueError("received value must come from received_value")
        if self.action is not None and not ACTION_CODE.fullmatch(self.action):
            raise ValueError("invalid action code")
        if self.target is not None and not ACTION_TARGET.fullmatch(self.target):
            raise ValueError("invalid action target")

    def _check_fields(self) -> None:
        required = set(_REQUIRED[self.code])
        optional = {"target"} if "action" in required else set()
        given = {
            item.name
            for item in fields(self)
            if item.name not in ("code", "module") and getattr(self, item.name) not in (None, ())
        }
        if missing := required - given:
            raise ValueError(f"{self.code}: missing {', '.join(sorted(missing))}")
        if extra := given - required - optional:
            raise ValueError(f"{self.code}: does not take {', '.join(sorted(extra))}")

    @property
    def key(self) -> tuple[str | None, ...]:
        """Anomalies with the same key are the same one, seen again."""
        return (self.module, self.source, self.code, self.field, self.action, self.target)


def describe(anomaly: Anomaly, translator: Translator) -> str:
    """The sentence shown to the user, in the language of the installation."""
    values: dict[str, str | int] = {}
    match anomaly.code:
        case AnomalyCode.SOURCE_MISSING | AnomalyCode.PARSE_ERROR:
            values = {"source": str(anomaly.source)}
        case AnomalyCode.SOURCE_UNREADABLE:
            reason = translator.text(f"anomaly.reason.{anomaly.reason}")
            values = {"source": str(anomaly.source), "reason": reason}
        case AnomalyCode.UNEXPECTED_VALUE | AnomalyCode.IMPLAUSIBLE_VALUE:
            value = (
                translator.text("anomaly.hidden_value")
                if anomaly.received is HIDDEN
                else str(anomaly.received)
            )
            values = {"field": str(anomaly.field), "value": value}
            if anomaly.code is AnomalyCode.UNEXPECTED_VALUE:
                values["expected"] = ", ".join(anomaly.expected)
        case AnomalyCode.SCHEMA_CHANGED:
            values = {"source": str(anomaly.source), "field": str(anomaly.field)}
        case AnomalyCode.SENSOR_ERROR | AnomalyCode.SENSOR_TIMEOUT:
            values = {"sensor": anomaly.module}
        case _:
            values = {"action": str(anomaly.action)}
            if anomaly.exit_code is not None:
                values["exit_code"] = anomaly.exit_code
            if anomaly.timeout_seconds is not None:
                values["timeout"] = anomaly.timeout_seconds
    suffix = ""
    if anomaly.target is not None:
        suffix = "_target"
        values["target"] = anomaly.target
    return translator.text(f"anomaly.message.{anomaly.code}{suffix}", **values)


@dataclass(frozen=True)
class AnomalyEntry:
    """One line of the anomaly list."""

    id: str
    anomaly: Anomaly  # the latest occurrence
    first_seen: datetime
    last_seen: datetime
    count: int


class AnomalyRegister:
    """Groups identical anomalies. Kept in memory: nothing is saved yet."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str | None, ...], AnomalyEntry] = {}

    def record(self, anomaly: Anomaly, seen_at: datetime) -> AnomalyEntry:
        if seen_at.utcoffset() is None:
            raise ValueError("seen_at must have a time zone")
        known = self._entries.get(anomaly.key)
        if known is None:
            digest = hashlib.sha256(json.dumps(anomaly.key).encode()).hexdigest()[:16]
            entry = AnomalyEntry(digest, anomaly, seen_at, seen_at, 1)
        else:
            entry = replace(known, anomaly=anomaly, last_seen=seen_at, count=known.count + 1)
        self._entries[anomaly.key] = entry
        return entry

    def get(self, key: tuple[str | None, ...]) -> AnomalyEntry | None:
        return self._entries.get(key)

    def remove(self, key: tuple[str | None, ...]) -> None:
        del self._entries[key]

    def entries(self) -> tuple[AnomalyEntry, ...]:
        """In the order in which they were first recorded."""
        return tuple(self._entries.values())
