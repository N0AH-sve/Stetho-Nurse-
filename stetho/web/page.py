"""What the status page shows, prepared in Python: the template only presents it."""

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from stetho.anomalies import HIDDEN, AnomalyEntry, describe
from stetho.durations import duration_text
from stetho.i18n import Translator
from stetho.results import SensorResult, Status
from stetho.web.report import View

_PROBLEMS = (Status.CRITICAL, Status.WARN, Status.UNKNOWN)  # most severe first


@dataclass(frozen=True)
class Moment:
    """A time as shown: how long ago, and the exact date on hover."""

    relative: str
    exact: str
    machine: str


@dataclass(frozen=True)
class Problem:
    status: Status
    rank: int  # 0 is the most severe
    source: str
    summary: str
    advice: str


@dataclass(frozen=True)
class AnomalyRow:
    module: str
    source: str
    code: str
    message: str
    received: str
    expected: str
    first_seen: Moment
    last_seen: Moment
    count: int


@dataclass(frozen=True)
class Page:
    status: Status
    summary: str
    checked: Moment | None
    problems: tuple[Problem, ...]
    sensors: tuple[SensorResult, ...]
    anomalies: tuple[AnomalyRow, ...]


def _moment(value: datetime, now: datetime, translator: Translator, zone: ZoneInfo) -> Moment:
    age = duration_text(now - value, translator)
    exact = value.astimezone(zone).strftime("%Y-%m-%d %H:%M:%S")
    return Moment(translator.text("page.ago", age=age), f"{exact} ({zone.key})", value.isoformat())


def _problems(sensors: tuple[SensorResult, ...]) -> tuple[Problem, ...]:
    found: list[Problem] = []
    for sensor in sensors:
        # The failing items say more than the card that sums them up.
        items = [item for item in sensor.items if item.status in _PROBLEMS and item.assessed]
        for item in items:
            found.append(
                Problem(
                    item.status,
                    _PROBLEMS.index(item.status),
                    f"{sensor.title} · {item.label}",
                    item.summary,
                    item.advice or "",
                )
            )
        covered = any(item.status is sensor.status for item in items)
        if sensor.assessed and sensor.status in _PROBLEMS and not covered:
            found.append(
                Problem(
                    sensor.status,
                    _PROBLEMS.index(sensor.status),
                    sensor.title,
                    sensor.summary,
                    sensor.advice or "",
                )
            )
    return tuple(sorted(found, key=lambda problem: problem.rank))


def _anomaly(
    entry: AnomalyEntry, now: datetime, translator: Translator, zone: ZoneInfo
) -> AnomalyRow:
    anomaly = entry.anomaly
    received = anomaly.received
    if received is HIDDEN:
        shown = translator.text("anomaly.hidden_value")
    else:
        shown = "" if received is None else str(received)
    return AnomalyRow(
        module=anomaly.module,
        source=anomaly.source or "",
        code=anomaly.code.value,
        message=describe(anomaly, translator)
        + ("" if anomaly.error is None else f" ({anomaly.error})"),
        received=shown,
        expected=", ".join(anomaly.expected),
        first_seen=_moment(entry.first_seen, now, translator, zone),
        last_seen=_moment(entry.last_seen, now, translator, zone),
        count=entry.count,
    )


def build_page(current: View, now: datetime, translator: Translator, zone: ZoneInfo) -> Page:
    checked = current.checked_at
    return Page(
        status=current.status,
        summary=current.summary,
        checked=None if checked is None else _moment(checked, now, translator, zone),
        problems=_problems(current.sensors),
        sensors=current.sensors,
        anomalies=tuple(_anomaly(entry, now, translator, zone) for entry in current.anomalies),
    )
