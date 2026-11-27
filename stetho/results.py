"""Statuses and the result format shared by all sensors.

Titles, labels and summaries are texts already in the language of the installation. A result
that breaks a rule below cannot be built: it raises `ValueError`. Messages never quote a value,
which may come from Hermes.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType

from stetho.anomalies import Anomaly, AnomalyCode
from stetho.patterns import ACTION_CODE, SENSOR_ID


class Status(StrEnum):
    OK = "ok"
    WARN = "warn"
    CRITICAL = "critical"
    INFO = "info"  # shown only: never a problem
    UNKNOWN = "unknown"  # STETHO could not check: never shown as healthy


# `info` is absent on purpose: it has no severity.
_SEVERITY = MappingProxyType({Status.OK: 0, Status.UNKNOWN: 1, Status.WARN: 2, Status.CRITICAL: 3})


def worst_status(statuses: Iterable[Status]) -> Status | None:
    """The most severe status, ignoring `info`. None if nothing else is left."""
    return max(
        (status for status in statuses if status in _SEVERITY),
        key=_SEVERITY.__getitem__,
        default=None,
    )


def overall_status(statuses: Iterable[Status]) -> Status:
    """The state of the whole page. `unknown` if nothing was checked."""
    return worst_status(statuses) or Status.UNKNOWN


def _require_text(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def _check_not_assessed(
    assessed: bool, status: Status, advice: str | None, actions: tuple[str, ...]
) -> None:
    if not assessed and (status is not Status.UNKNOWN or advice is not None or actions):
        raise ValueError("what was not assessed is unknown, without advice or action")


def _check_advice(advice: str | None) -> None:
    if advice is not None:
        _require_text(advice, "advice")


def _check_actions(actions: tuple[str, ...]) -> None:
    if not all(ACTION_CODE.fullmatch(code) for code in actions):
        raise ValueError("invalid action code")
    if len(set(actions)) != len(actions):
        raise ValueError("repeated action code")


@dataclass(frozen=True)
class SensorOutcome:
    """What a reading produces: the card, and the anomalies behind it."""

    result: "SensorResult"
    anomalies: tuple[Anomaly, ...]


@dataclass(frozen=True, kw_only=True)
class Detail:
    label: str
    value: str

    def __post_init__(self) -> None:
        _require_text(self.label, "label")


@dataclass(frozen=True, kw_only=True)
class Item:
    """One element followed by a sensor, such as a scheduled job. It has no sub-items."""

    id: str
    label: str
    status: Status
    summary: str
    advice: str | None = None  # what to do, if there is something to do
    details: tuple[Detail, ...] = ()
    actions: tuple[str, ...] = ()
    # False: not checked on purpose, because something it depends on is down. Not a problem.
    assessed: bool = True

    def __post_init__(self) -> None:
        _require_text(self.id, "id")
        _require_text(self.label, "label")
        _require_text(self.summary, "summary")
        _check_advice(self.advice)
        _check_actions(self.actions)
        _check_not_assessed(self.assessed, self.status, self.advice, self.actions)


@dataclass(frozen=True, kw_only=True)
class SensorResult:
    """What a sensor returns at each reading."""

    id: str
    title: str
    group: str
    status: Status
    summary: str
    checked_at: datetime
    advice: str | None = None  # what to do, if there is something to do
    details: tuple[Detail, ...] = ()
    items: tuple[Item, ...] = ()
    actions: tuple[str, ...] = ()
    anomalies: tuple[AnomalyCode, ...] = ()  # why the sensor may not be able to tell
    # False: nothing that was checked explains its status.
    assessed: bool = True

    def __post_init__(self) -> None:
        if not SENSOR_ID.fullmatch(self.id):
            raise ValueError("invalid sensor id")
        _require_text(self.title, "title")
        _require_text(self.group, "group")
        _require_text(self.summary, "summary")
        if self.checked_at.utcoffset() is None:
            raise ValueError("checked_at must have a time zone")
        _check_advice(self.advice)
        _check_actions(self.actions)
        if len(set(self.anomalies)) != len(self.anomalies):
            raise ValueError("repeated anomaly code")
        if len({item.id for item in self.items}) != len(self.items):
            raise ValueError("repeated item id")
        _check_not_assessed(self.assessed, self.status, self.advice, self.actions)
        if not self.assessed and self.anomalies:
            raise ValueError("a sensor that was not assessed has no anomaly of its own")
        if not self.assessed and any(
            item.assessed and item.status not in (Status.OK, Status.INFO) for item in self.items
        ):
            raise ValueError("a sensor that was not assessed cannot hold an assessed problem")
        self._check_status_covers_items()

    def _check_status_covers_items(self) -> None:
        worst = worst_status(item.status for item in self.items)
        if worst is None or worst is Status.OK:
            return
        if _SEVERITY.get(self.status, -1) < _SEVERITY[worst]:
            raise ValueError("the sensor status must not be less severe than its items")
