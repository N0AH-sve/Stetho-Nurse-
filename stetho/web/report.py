"""What the API says about the latest pass of the probe, in the shapes of the contract.

A report that is too old must not look healthy: its statuses become unknown, and everything that
could mislead is replaced or emptied.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime

from stetho.anomalies import HIDDEN, AnomalyEntry, describe
from stetho.config import ProbeSettings
from stetho.i18n import Translator
from stetho.probe import ProbeReport
from stetho.results import Detail, Item, SensorResult, Status

type Monotonic = Callable[[], float]
type Json = dict[str, object]


def stale_after_seconds(probe: ProbeSettings) -> int:
    """The longer of three intervals, or the sensor time limit plus one interval."""
    return max(3 * probe.interval_seconds, probe.sensor_timeout_seconds + probe.interval_seconds)


class ReportStore:
    """The latest report of the probe. Written by its thread, read by the requests."""

    def __init__(self, monotonic: Monotonic = time.monotonic) -> None:
        self._monotonic = monotonic
        # Report and time of arrival, replaced as a whole: a reader never sees half of it.
        self._latest: tuple[ProbeReport, float] | None = None
        self.failed = False  # the startup or the shutdown of the application failed

    def publish(self, report: ProbeReport) -> None:
        self._latest = (report, self._monotonic())

    @property
    def latest(self) -> ProbeReport | None:
        return None if self._latest is None else self._latest[0]

    def read(self, stale_after: float) -> tuple[ProbeReport | None, bool]:
        """The latest report, and whether it is stale."""
        latest = self._latest
        if latest is None:
            return None, False
        report, arrived = latest
        # A clock that cannot be set back: changing the time of the server hides nothing.
        return report, self._monotonic() - arrived > stale_after


def _outdated_item(item: Item, summary: str) -> Item:
    return replace(
        item, status=Status.UNKNOWN, summary=summary, advice=None, details=(), actions=()
    )


def _outdated(sensor: SensorResult, summary: str) -> SensorResult:
    return replace(
        sensor,
        status=Status.UNKNOWN,
        summary=summary,
        advice=None,
        details=(),
        actions=(),
        items=tuple(_outdated_item(item, summary) for item in sensor.items),
    )


@dataclass(frozen=True)
class View:
    """The latest report as it may be shown now."""

    report: ProbeReport | None  # None before the first pass
    stale: bool
    sensors: tuple[SensorResult, ...]
    status: Status
    summary: str  # the sentence of the banner

    @property
    def anomalies(self) -> tuple[AnomalyEntry, ...]:
        return () if self.report is None else self.report.anomalies

    @property
    def checked_at(self) -> datetime | None:
        return None if self.report is None else self.report.checked_at


def _banner(status: Status, sensors: tuple[SensorResult, ...], translator: Translator) -> str:
    if status is Status.OK:
        return translator.text("banner.all_ok")
    worst = next((sensor for sensor in sensors if sensor.status is status), None)
    if worst is None:
        return translator.text("banner.nothing_checked")
    return translator.text("banner.problem", title=worst.title, summary=worst.summary)


def view(report: ProbeReport | None, translator: Translator, *, stale: bool) -> View:
    if report is None:
        return View(None, False, (), Status.UNKNOWN, translator.text("banner.nothing_checked"))
    if stale:
        outdated = translator.text("banner.stale")
        sensors = tuple(_outdated(sensor, outdated) for sensor in report.sensors)
        return View(report, True, sensors, Status.UNKNOWN, outdated)
    return View(
        report,
        False,
        report.sensors,
        report.status,
        _banner(report.status, report.sensors, translator),
    )


def _moment(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _details(details: tuple[Detail, ...]) -> list[Json]:
    return [{"label": detail.label, "value": detail.value} for detail in details]


def _item_json(item: Item) -> Json:
    return {
        "id": item.id,
        "label": item.label,
        "status": item.status.value,
        "summary": item.summary,
        "advice": item.advice,
        "details": _details(item.details),
        "actions": list(item.actions),
    }


def sensor_json(sensor: SensorResult, *, stale: bool) -> Json:
    return {
        "id": sensor.id,
        "title": sensor.title,
        "group": sensor.group,
        "status": sensor.status.value,
        "summary": sensor.summary,
        "advice": sensor.advice,
        "checked_at": _moment(sensor.checked_at),
        "stale": stale,
        "details": _details(sensor.details),
        "items": [_item_json(item) for item in sensor.items],
        "actions": list(sensor.actions),
        "anomalies": [code.value for code in sensor.anomalies],
    }


def status_json(current: View) -> Json:
    return {
        "status": current.status.value,
        "summary": current.summary,
        "checked_at": _moment(current.checked_at),
        "stale": current.stale,
        "anomaly_count": len(current.anomalies),
        "sensors": [sensor_json(sensor, stale=current.stale) for sensor in current.sensors],
    }


def anomaly_json(entry: AnomalyEntry, translator: Translator) -> Json:
    anomaly = entry.anomaly
    hidden = anomaly.received is HIDDEN
    return {
        "id": entry.id,
        "code": anomaly.code.value,
        "module": anomaly.module,
        "message": describe(anomaly, translator),
        "source": anomaly.source,
        "field": anomaly.field,
        "received": None if hidden else anomaly.received,
        "received_hidden": hidden,
        "expected": list(anomaly.expected),
        "reason": None if anomaly.reason is None else anomaly.reason.value,
        "error": None if anomaly.error is None else str(anomaly.error),
        "action": anomaly.action,
        "target": anomaly.target,
        "exit_code": anomaly.exit_code,
        "timeout_seconds": anomaly.timeout_seconds,
        "first_seen": _moment(entry.first_seen),
        "last_seen": _moment(entry.last_seen),
        "count": entry.count,
    }
