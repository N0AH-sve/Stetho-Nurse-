"""Runs the sensors periodically, each one isolated and limited in time.

A sensor that crashes or is too slow reports `unknown` and never stops the others. Nothing read
from Hermes is written to the log: anomalies by their place only, and internal errors by their
type and their calls in STETHO, never their message.
"""

import logging
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from stetho.anomalies import Anomaly, AnomalyCode, AnomalyEntry, AnomalyRegister, describe
from stetho.config import Config
from stetho.errors import summarise_error
from stetho.i18n import Translator
from stetho.modules.gateway.sensor import GatewaySensor
from stetho.modules.platforms.sensor import PlatformsSensor
from stetho.results import Detail, SensorOutcome, SensorResult, Status, overall_status
from stetho.sources import Sleep

_LOG = logging.getLogger(__name__)
# Share of the time limit of a sensor that may be spent waiting before new attempts.
_WAIT_SHARE = 0.5
_FAILURES = frozenset({AnomalyCode.SENSOR_ERROR, AnomalyCode.SENSOR_TIMEOUT})

type Clock = Callable[[], datetime]


class Sensor(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def title(self) -> str: ...

    @property
    def group(self) -> str: ...

    def read(self, now: datetime, sleep: Sleep) -> SensorOutcome:
        """Read the sources. `sleep` is the only way to wait between two attempts."""
        ...


@dataclass(frozen=True)
class ProbeReport:
    """The state after one pass. It does not change afterwards."""

    status: Status
    checked_at: datetime
    sensors: tuple[SensorResult, ...]
    anomalies: tuple[AnomalyEntry, ...]


class LimitedSleep:
    """Waits until a total is reached; after that, new attempts are made at once."""

    def __init__(self, total_seconds: float, sleep: Sleep) -> None:
        self._remaining = total_seconds
        self._sleep = sleep

    def __call__(self, seconds: float) -> None:
        wait = min(seconds, self._remaining)
        if wait > 0:
            self._remaining -= wait
            self._sleep(wait)


class _Run:
    """One reading of one sensor, in its own thread.

    The thread cannot be stopped: a late one is left to finish and its result is ignored.
    """

    def __init__(self, sensor: Sensor, now: datetime, sleep: Sleep) -> None:
        self.outcome: SensorOutcome | None = None
        self.error: BaseException | None = None
        self._thread = threading.Thread(
            target=self._read, args=(sensor, now, sleep), name=f"sensor-{sensor.id}", daemon=True
        )
        self._thread.start()

    def _read(self, sensor: Sensor, now: datetime, sleep: Sleep) -> None:
        try:
            self.outcome = sensor.read(now, sleep)
        # Everything is caught: Python would otherwise print the message of the error.
        except BaseException as error:  # noqa: BLE001
            self.error = error

    def wait(self, seconds: float) -> None:
        self._thread.join(max(seconds, 0))

    @property
    def running(self) -> bool:
        return self._thread.is_alive()


class Probe:
    """Not thread-safe: one caller runs the passes."""

    def __init__(
        self,
        sensors: Sequence[Sensor],
        translator: Translator,
        *,
        timeout_seconds: float,
        clock: Clock,
        sleep: Sleep = time.sleep,
    ) -> None:
        if len({sensor.id for sensor in sensors}) != len(sensors):
            raise ValueError("repeated sensor id")
        self._sensors = tuple(sensors)
        self._translator = translator
        self._timeout = timeout_seconds
        self._clock = clock
        self._sleep = sleep
        self._register = AnomalyRegister()
        self._late: dict[str, _Run] = {}

    def run_once(self) -> ProbeReport:
        """Run every sensor at the same time and wait for them up to the time limit."""
        now = self._clock()
        deadline = time.monotonic() + self._timeout
        runs: dict[str, _Run | None] = {}
        for sensor in self._sensors:
            late = self._late.get(sensor.id)
            if late is not None and late.running:
                runs[sensor.id] = None  # never two readings of a sensor at once
                continue
            self._late.pop(sensor.id, None)
            limited = LimitedSleep(self._timeout * _WAIT_SHARE, self._sleep)
            runs[sensor.id] = _Run(sensor, now, limited)
        results = tuple(
            self._collect(sensor, runs[sensor.id], deadline, now) for sensor in self._sensors
        )
        return ProbeReport(
            overall_status(result.status for result in results),
            now,
            results,
            self._register.entries(),
        )

    def _collect(
        self, sensor: Sensor, run: _Run | None, deadline: float, now: datetime
    ) -> SensorResult:
        if run is not None:
            run.wait(deadline - time.monotonic())
            if run.running:
                self._late[sensor.id] = run
            elif run.outcome is None:
                anomaly = Anomaly(
                    code=AnomalyCode.SENSOR_ERROR,
                    module=sensor.id,
                    error=summarise_error(run.error or RuntimeError()),
                )
                return self._failed(sensor, anomaly, now)
            else:
                # Not assessed: nothing was checked again, so earlier anomalies stay.
                if run.outcome.result.assessed:
                    self._settle(sensor.id, run.outcome.anomalies, now)
                return run.outcome.result
        return self._failed(sensor, Anomaly(code=AnomalyCode.SENSOR_TIMEOUT, module=sensor.id), now)

    def _settle(self, module: str, anomalies: Sequence[Anomaly], now: datetime) -> None:
        """After a complete reading, the anomalies that were not seen again are over."""
        # Counted once per reading, even if seen several times in it.
        seen = {anomaly.key: anomaly for anomaly in anomalies}
        for entry in self._register.entries():
            if entry.anomaly.module == module and entry.anomaly.key not in seen:
                self._end(entry.anomaly)
        for anomaly in seen.values():
            self._record(anomaly, now)

    def _failed(self, sensor: Sensor, anomaly: Anomaly, now: datetime) -> SensorResult:
        # Nothing was checked: earlier anomalies stay, except another failure of the sensor.
        for entry in self._register.entries():
            other = entry.anomaly
            if other.module == sensor.id and other.code in _FAILURES - {anomaly.code}:
                self._end(other)
        self._record(anomaly, now)
        details: tuple[Detail, ...] = ()
        if anomaly.error is not None:
            label = self._translator.text("probe.detail.error")
            details = (Detail(label=label, value=str(anomaly.error)),)
        return SensorResult(
            id=sensor.id,
            title=sensor.title,
            group=sensor.group,
            status=Status.UNKNOWN,
            summary=describe(anomaly, self._translator),
            advice=self._translator.text(f"probe.advice.{anomaly.code}"),
            checked_at=now,
            details=details,
            anomalies=(anomaly.code,),
        )

    def _record(self, anomaly: Anomaly, now: datetime) -> None:
        known = self._register.get(anomaly.key)
        self._register.record(anomaly, now)
        # Written when it appears, or when the internal error is a different one.
        if known is not None and known.anomaly.error == anomaly.error:
            return
        _LOG.warning("anomaly started: %s", _place(anomaly))
        if anomaly.error is not None:
            calls = "".join(f"\n  {call}" for call in anomaly.error.calls)
            _LOG.warning("internal error: %s%s", anomaly.error.error_type, calls)

    def _end(self, anomaly: Anomaly) -> None:
        self._register.remove(anomaly.key)
        _LOG.info("anomaly ended: %s", _place(anomaly))

    def run_forever(
        self,
        interval_seconds: float,
        stop: threading.Event,
        publish: Callable[[ProbeReport], None],
    ) -> None:
        """Run a pass at each interval until `stop` is set. A failed pass does not end it."""
        while not stop.is_set():
            started = time.monotonic()
            try:
                publish(self.run_once())
            except Exception as error:  # noqa: BLE001 - monitoring must go on
                summary = summarise_error(error)
                calls = "".join(f"\n  {call}" for call in summary.calls)
                _LOG.error("probe pass failed: %s%s", summary.error_type, calls)
            stop.wait(max(interval_seconds - (time.monotonic() - started), 0))


def _place(anomaly: Anomaly) -> str:
    """Where an anomaly is, without any value read from outside."""
    parts = [f"module={anomaly.module}", f"code={anomaly.code}"]
    for name in ("source", "field", "action", "target"):
        value = getattr(anomaly, name)
        if value is not None:
            parts.append(f"{name}={value!r}")
    return " ".join(parts)


def build_sensors(config: Config, translator: Translator) -> tuple[Sensor, ...]:
    """The sensors of the enabled modules."""
    sensors: list[Sensor] = []
    if config.modules.gateway.enabled:
        sensors.append(GatewaySensor(config, translator))
    if config.modules.platforms.enabled:
        sensors.append(PlatformsSensor(config, translator))
    return tuple(sensors)
