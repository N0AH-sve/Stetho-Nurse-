import logging
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from stetho.anomalies import Anomaly, AnomalyCode
from stetho.config import Config, Language, parse_config
from stetho.i18n import load_translator
from stetho.probe import LimitedSleep, Probe, ProbeReport, Sensor, build_sensors
from stetho.results import SensorOutcome, SensorResult, Status
from stetho.sources import Sleep

FIXTURES = Path(__file__).parents[1] / "fixtures" / "hermes-0.21"
EXAMPLE = Path(__file__).parents[2] / "config" / "stetho.example.yaml"
NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
MARKER = "made-up-private-text"
TRANSLATOR = load_translator(Language.EN)

type Behaviour = Callable[[datetime, Sleep], SensorOutcome]


def outcome(
    sensor_id: str = "one", status: Status = Status.OK, *anomalies: Anomaly
) -> SensorOutcome:
    result = SensorResult(
        id=sensor_id,
        title="Title",
        group="Group",
        status=status,
        summary="Summary",
        checked_at=NOW,
    )
    return SensorOutcome(result, anomalies)


def parse_error(module: str = "one", source: str = "state.json") -> Anomaly:
    return Anomaly(code=AnomalyCode.PARSE_ERROR, module=module, source=source)


class FakeSensor:
    """Does what `behaviour` says; it can be changed between two passes."""

    title = "Title"
    group = "Group"

    def __init__(self, sensor_id: str = "one", behaviour: Behaviour | None = None) -> None:
        self.id = sensor_id
        self.behaviour = behaviour or self.healthy
        self.readings = 0

    def healthy(self, _now: datetime, _sleep: Sleep) -> SensorOutcome:
        return outcome(self.id)

    def finds(self, *anomalies: Anomaly) -> None:
        self.behaviour = lambda _now, _sleep: outcome(self.id, Status.OK, *anomalies)

    def read(self, now: datetime, sleep: Sleep) -> SensorOutcome:
        self.readings += 1
        return self.behaviour(now, sleep)


def crash(_now: datetime, _sleep: Sleep) -> SensorOutcome:
    raise KeyError(MARKER)


@pytest.fixture
def release() -> Iterator[threading.Event]:
    """Lets the blocked sensors finish at the end of a test."""
    event = threading.Event()
    yield event
    event.set()


def blocked(event: threading.Event) -> Behaviour:
    def behaviour(_now: datetime, _sleep: Sleep) -> SensorOutcome:
        event.wait(10)
        return outcome()

    return behaviour


def probe(*sensors: Sensor, timeout: float = 5) -> Probe:
    return Probe(sensors, TRANSLATOR, timeout_seconds=timeout, clock=lambda: NOW)


def entry_codes(report: ProbeReport) -> list[tuple[str, AnomalyCode, int]]:
    return [(e.anomaly.module, e.anomaly.code, e.count) for e in report.anomalies]


def finish(runner: Probe, release: threading.Event) -> None:
    """Let the late readings end before the next pass."""
    release.set()
    for run in runner._late.values():
        run.wait(5)


def test_pass_gathers_the_results_in_order() -> None:
    report = probe(FakeSensor("two"), FakeSensor("one")).run_once()

    assert [result.id for result in report.sensors] == ["two", "one"]
    assert (report.status, report.checked_at, report.anomalies) == (Status.OK, NOW, ())


def test_without_a_sensor_nothing_is_healthy() -> None:
    assert probe().run_once().status is Status.UNKNOWN


def test_two_sensors_cannot_share_an_id() -> None:
    with pytest.raises(ValueError, match="repeated sensor id"):
        probe(FakeSensor(), FakeSensor())


def test_crashing_sensor_is_isolated() -> None:
    report = probe(FakeSensor("one", crash), FakeSensor("two")).run_once()

    failed, healthy = report.sensors
    assert (failed.status, healthy.status, report.status) == (
        Status.UNKNOWN,
        Status.OK,
        Status.UNKNOWN,
    )
    assert failed.summary == "The one sensor met an error."
    assert (failed.title, failed.group) == ("Title", "Group")
    assert failed.anomalies == (AnomalyCode.SENSOR_ERROR,)
    assert failed.details[0].label == "Internal error"
    assert failed.details[0].value.startswith("KeyError")


@pytest.mark.parametrize("error", [KeyError, SystemExit, KeyboardInterrupt, RecursionError])
def test_message_of_an_error_appears_nowhere(
    error: type[BaseException],
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def behaviour(_now: datetime, _sleep: Sleep) -> SensorOutcome:
        raise error(MARKER)

    caplog.set_level(logging.DEBUG)

    report = probe(FakeSensor("one", behaviour)).run_once()

    assert report.sensors[0].anomalies == (AnomalyCode.SENSOR_ERROR,)
    printed = capsys.readouterr()
    assert MARKER not in repr(report) + caplog.text + printed.out + printed.err


def test_internal_error_is_logged_with_its_calls_in_stetho(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def behaviour(_now: datetime, _sleep: Sleep) -> SensorOutcome:
        LimitedSleep(1, crash_sleep)(1)
        return outcome()

    def crash_sleep(_: float) -> None:
        raise OSError(MARKER)

    probe(FakeSensor("one", behaviour)).run_once()

    assert "internal error: OSError" in caplog.text
    assert "stetho/probe.py:" in caplog.text
    assert MARKER not in caplog.text
    assert str(Path(__file__).parent) not in caplog.text


def test_slow_sensor_is_unknown_and_does_not_delay_others(release: threading.Event) -> None:
    report = probe(FakeSensor("one", blocked(release)), FakeSensor("two"), timeout=0.3).run_once()

    slow, healthy = report.sensors
    assert (slow.status, healthy.status) == (Status.UNKNOWN, Status.OK)
    assert slow.summary == "The one sensor did not answer in time."
    assert slow.anomalies == (AnomalyCode.SENSOR_TIMEOUT,)
    assert slow.details == ()


def test_slow_sensor_is_not_started_twice(release: threading.Event) -> None:
    sensor = FakeSensor("one", blocked(release))
    runner = probe(sensor, timeout=0.05)

    runner.run_once()
    report = runner.run_once()

    assert sensor.readings == 1
    assert entry_codes(report) == [("one", AnomalyCode.SENSOR_TIMEOUT, 2)]


def test_late_result_is_ignored_and_the_sensor_starts_again(release: threading.Event) -> None:
    sensor = FakeSensor("one", blocked(release))
    runner = probe(sensor, timeout=0.2)
    runner.run_once()
    finish(runner, release)
    sensor.behaviour = lambda _now, _sleep: outcome("one", Status.WARN)

    report = runner.run_once()

    assert sensor.readings == 2
    assert (report.status, report.anomalies) == (Status.WARN, ())


def test_time_limit_is_shared_not_added(release: threading.Event) -> None:
    sensors = [FakeSensor(name, blocked(release)) for name in ("a", "b", "c", "d")]
    started = datetime.now(UTC)

    probe(*sensors, timeout=0.3).run_once()

    assert datetime.now(UTC) - started < timedelta(seconds=1)


def test_anomaly_lasts_as_long_as_it_is_seen(caplog: pytest.LogCaptureFixture) -> None:
    sensor = FakeSensor()
    runner = probe(sensor)
    caplog.set_level(logging.INFO)
    sensor.finds(parse_error(), parse_error())

    runner.run_once()
    second = runner.run_once()
    sensor.finds()
    third = runner.run_once()

    assert entry_codes(second) == [("one", AnomalyCode.PARSE_ERROR, 2)]
    assert third.anomalies == ()
    assert [record.getMessage() for record in caplog.records] == [
        "anomaly started: module=one code=parse_error source='state.json'",
        "anomaly ended: module=one code=parse_error source='state.json'",
    ]


def test_report_does_not_change_afterwards() -> None:
    sensor = FakeSensor()
    runner = probe(sensor)
    sensor.finds(parse_error())
    first = runner.run_once()
    sensor.finds()

    runner.run_once()

    assert len(first.anomalies) == 1


def test_anomalies_of_another_sensor_are_not_ended() -> None:
    one, two = FakeSensor("one"), FakeSensor("two")
    one.finds(parse_error("one"))
    two.finds(parse_error("two"))
    runner = probe(one, two)
    runner.run_once()
    one.finds()

    report = runner.run_once()

    assert entry_codes(report) == [("two", AnomalyCode.PARSE_ERROR, 2)]


def test_failure_keeps_earlier_anomalies_until_a_complete_reading(
    release: threading.Event,
) -> None:
    sensor = FakeSensor()
    sensor.finds(parse_error())
    runner = probe(sensor, timeout=0.2)
    runner.run_once()

    sensor.behaviour = crash
    crashed = runner.run_once()
    sensor.behaviour = blocked(release)
    slow = runner.run_once()
    finish(runner, release)
    sensor.behaviour = sensor.healthy
    healthy = runner.run_once()

    assert entry_codes(crashed) == [
        ("one", AnomalyCode.PARSE_ERROR, 1),
        ("one", AnomalyCode.SENSOR_ERROR, 1),
    ]
    assert entry_codes(slow) == [
        ("one", AnomalyCode.PARSE_ERROR, 1),
        ("one", AnomalyCode.SENSOR_TIMEOUT, 1),
    ]
    assert healthy.anomalies == ()


def test_repeated_crash_is_logged_once_until_the_error_changes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sensor = FakeSensor("one", crash)
    runner = probe(sensor)

    runner.run_once()
    runner.run_once()
    assert caplog.text.count("internal error") == 1

    def other(_now: datetime, _sleep: Sleep) -> SensorOutcome:
        raise ValueError(MARKER)

    sensor.behaviour = other
    report = runner.run_once()

    assert caplog.text.count("internal error") == 2
    assert entry_codes(report) == [("one", AnomalyCode.SENSOR_ERROR, 3)]


def test_waits_are_limited_in_total() -> None:
    waits: list[float] = []
    sleep = LimitedSleep(2.5, waits.append)

    for delay in (0.2, 0.5, 1.0, 1.0, 0.2, 0.5):
        sleep(delay)

    assert waits == pytest.approx([0.2, 0.5, 1.0, 0.8])


def test_each_reading_gets_half_of_the_time_limit_to_wait() -> None:
    waits: list[float] = []

    def behaviour(_now: datetime, sleep: Sleep) -> SensorOutcome:
        for _ in range(10):
            sleep(1)
        return outcome()

    runner = Probe(
        [FakeSensor("one", behaviour)],
        TRANSLATOR,
        timeout_seconds=5,
        clock=lambda: NOW,
        sleep=waits.append,
    )

    runner.run_once()
    runner.run_once()

    assert waits == [1, 1, 0.5, 1, 1, 0.5]


def test_passes_are_repeated_until_asked_to_stop() -> None:
    stop = threading.Event()
    reports: list[ProbeReport] = []

    def publish(report: ProbeReport) -> None:
        reports.append(report)
        if len(reports) == 3:
            stop.set()

    probe(FakeSensor()).run_forever(0.001, stop, publish)

    assert len(reports) == 3


def test_failed_pass_does_not_end_the_monitoring(caplog: pytest.LogCaptureFixture) -> None:
    stop = threading.Event()
    calls: list[ProbeReport] = []

    def publish(report: ProbeReport) -> None:
        calls.append(report)
        if len(calls) == 1:
            raise RuntimeError(MARKER)
        stop.set()

    probe(FakeSensor()).run_forever(0.001, stop, publish)

    assert len(calls) == 2
    assert "probe pass failed: RuntimeError" in caplog.text
    assert MARKER not in caplog.text


def test_interval_is_counted_from_the_start_of_a_pass() -> None:
    waits: list[float] = []

    class Stop(threading.Event):
        def wait(self, timeout: float | None = None) -> bool:
            assert timeout is not None
            waits.append(timeout)
            self.set()
            return True

    probe(FakeSensor()).run_forever(30, Stop(), lambda _: None)

    assert 25 < waits[0] <= 30


def config(data_dir: Path, *disabled: str) -> Config:
    document = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    document["hermes"]["data_dir"] = str(data_dir)
    for name in disabled:
        document["modules"][name]["enabled"] = False
    return parse_config(document)


@pytest.mark.parametrize(
    ("scenario", "status"),
    # In `degraded`, one platform is in a fatal error.
    [("healthy", Status.OK), ("degraded", Status.CRITICAL), ("stopped", Status.CRITICAL)],
)
def test_modules_from_their_files_to_the_report(scenario: str, status: Status) -> None:
    sensors = build_sensors(config(FIXTURES / scenario), TRANSLATOR)

    report = probe(*sensors).run_once()

    assert report.status is status
    assert [result.id for result in report.sensors] == ["gateway", "platforms"]


def test_gateway_anomalies_reach_the_report_and_end(tmp_path: Path) -> None:
    broken = (FIXTURES / "invalid" / "truncated-json" / "gateway_state.json").read_bytes()
    (tmp_path / "gateway_state.json").write_bytes(broken)
    sensors = build_sensors(config(tmp_path, "platforms"), TRANSLATOR)
    runner = Probe(sensors, TRANSLATOR, timeout_seconds=5, clock=lambda: NOW, sleep=lambda _: None)
    assert entry_codes(runner.run_once()) == [("gateway", AnomalyCode.PARSE_ERROR, 1)]
    healthy = (FIXTURES / "healthy" / "gateway_state.json").read_bytes()
    (tmp_path / "gateway_state.json").write_bytes(healthy)

    report = runner.run_once()

    assert (report.status, report.anomalies) == (Status.OK, ())


@pytest.mark.parametrize(
    ("disabled", "expected"),
    [(("gateway",), ["platforms"]), (("platforms",), ["gateway"]), (("gateway", "platforms"), [])],
)
def test_disabled_module_has_no_sensor(disabled: tuple[str, ...], expected: list[str]) -> None:
    sensors = build_sensors(config(FIXTURES / "healthy", *disabled), TRANSLATOR)

    assert [sensor.id for sensor in sensors] == expected


def test_anomalies_stay_while_a_sensor_is_not_assessed() -> None:
    sensor = FakeSensor()
    sensor.finds(parse_error())
    runner = probe(sensor)
    runner.run_once()

    def not_assessed(_now: datetime, _sleep: Sleep) -> SensorOutcome:
        card = SensorResult(
            id="one",
            title="Title",
            group="Group",
            status=Status.UNKNOWN,
            summary="Not checked",
            checked_at=NOW,
            assessed=False,
        )
        return SensorOutcome(card, ())

    sensor.behaviour = not_assessed
    skipped = runner.run_once()
    sensor.behaviour = sensor.healthy
    checked = runner.run_once()

    assert entry_codes(skipped) == [("one", AnomalyCode.PARSE_ERROR, 1)]
    assert checked.anomalies == ()
