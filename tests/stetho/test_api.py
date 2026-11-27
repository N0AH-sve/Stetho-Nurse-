"""The reading routes, and every response checked against the contract.

The application is called directly, with a report store and a clock controlled by the test.
"""

import base64
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import anyio
import pytest
import yaml
from openapi_schema_validator import OAS31Validator, oas31_format_checker

from stetho.anomalies import HIDDEN, Anomaly, AnomalyCode, AnomalyRegister, UnreadableReason
from stetho.config import Config, Language, ProbeSettings, parse_config
from stetho.errors import Call, ErrorSummary
from stetho.i18n import load_translator
from stetho.probe import Probe, ProbeReport, Sensor, build_sensors
from stetho.results import Detail, Item, SensorOutcome, SensorResult, Status
from stetho.sources import Sleep
from stetho.web.app import create_app
from stetho.web.report import ReportStore, stale_after_seconds, view

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "hermes-0.21"
INVALID = sorted(f"invalid/{path.name}" for path in (FIXTURES / "invalid").iterdir())
SCENARIOS = ["healthy", "degraded", "stopped", "startup-failed", *INVALID]
NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
CONTRACT = yaml.safe_load((ROOT / "stetho" / "openapi.yaml").read_text(encoding="utf-8"))
MARKER = "made-up-private-text"
PHRASE = "made-up-password-long-enough"


def config(data_dir: Path = FIXTURES / "healthy", language: str = "en") -> Config:
    document = yaml.safe_load((ROOT / "config" / "stetho.example.yaml").read_text("utf-8"))
    document["language"] = language
    document["auth"]["password"] = PHRASE
    document["hermes"]["data_dir"] = str(data_dir)
    return parse_config(document)


class Time:
    def __init__(self) -> None:
        self.seconds = 1000.0

    def __call__(self) -> float:
        return self.seconds


@dataclass(frozen=True)
class Answer:
    status: int
    content_type: str
    body: bytes

    @property
    def json(self) -> Any:
        return json.loads(self.body)


class Api:
    """The protected application, without its probe: the test publishes the reports."""

    def __init__(self, settings: Config | None = None) -> None:
        self.config = settings or config()
        self.time = Time()
        self.store = ReportStore(self.time)
        self.translator = load_translator(self.config.language)
        probe = Probe((), self.translator, timeout_seconds=1, clock=lambda: NOW)
        self.app = create_app(self.config, self.translator, probe, self.store)

    def get(
        self, path: str, query: str = "", *, method: str = "GET", authenticated: bool = True
    ) -> Answer:
        credentials = base64.b64encode(f"u:{PHRASE}".encode())
        headers = [(b"host", b"localhost")]
        if authenticated:
            headers.append((b"authorization", b"Basic " + credentials))
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "root_path": "",
            "query_string": query.encode(),
            "headers": headers,
            "client": ("127.0.0.1", 1),
            "server": ("127.0.0.1", 80),
        }
        sent: list[Any] = []

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: Any) -> None:
            sent.append(message)

        anyio.run(self.app, scope, receive, send)
        response_headers = dict(sent[0]["headers"])
        body = b"".join(message.get("body", b"") for message in sent[1:])
        return Answer(sent[0]["status"], response_headers[b"content-type"].decode(), body)


def errors_against_contract(payload: object, path: str, status: int) -> list[str]:
    response = CONTRACT["paths"][path]["get"]["responses"][str(status)]
    if "$ref" in response:
        response = CONTRACT["components"]["responses"][response["$ref"].rsplit("/", 1)[1]]
    schema = response["content"]["application/json"]["schema"]
    validator = OAS31Validator(
        {**schema, "components": CONTRACT["components"]}, format_checker=oas31_format_checker
    )
    return [f"{list(error.path)}: {error.message}" for error in validator.iter_errors(payload)]


def conforming(api: Api, path: str, query: str = "", *, template: str = "") -> Any:
    answer = api.get(path, query)
    assert answer.content_type.startswith("application/json")
    assert errors_against_contract(answer.json, template or path, answer.status) == []
    return answer.json


def check_every_read(api: Api) -> tuple[Any, Any]:
    status = conforming(api, "/api/v1/status")
    anomalies = conforming(api, "/api/v1/anomalies")
    for sensor in status["sensors"]:
        one = conforming(
            api, f"/api/v1/sensors/{sensor['id']}", template="/api/v1/sensors/{sensor_id}"
        )
        assert one == sensor
    assert status["anomaly_count"] == len(anomalies["anomalies"])
    assert (status["checked_at"], status["stale"]) == (
        anomalies["checked_at"],
        anomalies["stale"],
    )
    return status, anomalies


def run_probe(settings: Config, sensors: Sequence[Sensor] | None = None) -> ProbeReport:
    translator = load_translator(settings.language)
    return Probe(
        build_sensors(settings, translator) if sensors is None else sensors,
        translator,
        timeout_seconds=5,
        clock=lambda: NOW,
        sleep=lambda _: None,
    ).run_once()


def test_the_contract_check_can_fail() -> None:
    wrong = {"status": "ok", "summary": "", "checked_at": "yesterday", "extra": 1}

    messages = " ".join(errors_against_contract(wrong, "/api/v1/status", 200))

    for expected in ("extra", "yesterday", "sensors", "stale", "summary"):
        assert expected in messages


def test_before_the_first_pass_nothing_is_healthy() -> None:
    status, anomalies = check_every_read(Api())

    assert status == {
        "status": "unknown",
        "summary": "Nothing could be checked",
        "checked_at": None,
        "stale": False,
        "anomaly_count": 0,
        "sensors": [],
    }
    assert anomalies == {"checked_at": None, "stale": False, "anomalies": []}


@pytest.mark.parametrize("language", [item.value for item in Language])
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_every_scenario_follows_the_contract(scenario: str, language: str) -> None:
    api = Api(config(FIXTURES / scenario, language))
    report = run_probe(api.config)
    api.store.publish(report)

    status, _ = check_every_read(api)

    assert status["status"] == report.status.value
    assert status["checked_at"] == "2026-01-15T12:00:00+00:00"


def test_healthy_report() -> None:
    api = Api()
    api.store.publish(run_probe(api.config))

    status, _ = check_every_read(api)

    assert (status["status"], status["summary"]) == ("ok", "Everything is working")
    (gateway,) = status["sensors"]
    assert (gateway["advice"], gateway["stale"]) == (None, False)


def test_banner_names_the_most_severe_problem_with_its_advice() -> None:
    api = Api(config(FIXTURES / "stopped", "fr"))
    api.store.publish(run_probe(api.config))

    status, _ = check_every_read(api)

    assert status["summary"] == "Hermes (gateway) : Hermes est arrêté"
    assert status["sensors"][0]["advice"] == (
        "Lisez les journaux de Hermes, puis redémarrez le gateway de Hermes."
    )


class FakeSensor:
    title = "Title"
    group = "Group"

    def __init__(self, sensor_id: str, result: SensorResult | None, *found: Anomaly) -> None:
        self.id = sensor_id
        self.result = result
        self.found = found

    def read(self, _now: datetime, _sleep: Sleep) -> SensorOutcome:
        if self.result is None:
            raise KeyError(MARKER)
        return SensorOutcome(self.result, self.found)


def result(sensor_id: str, status: Status, **changes: Any) -> SensorResult:
    values: dict[str, Any] = {
        "id": sensor_id,
        "title": f"Title {sensor_id}",
        "group": "Group",
        "status": status,
        "summary": f"Summary {sensor_id}",
        "checked_at": NOW,
    }
    return SensorResult(**(values | changes))


def every_anomaly() -> list[Anomaly]:
    action: dict[str, Any] = {"action": "RERUN_JOB"}
    fields: dict[AnomalyCode, dict[str, Any]] = {
        AnomalyCode.SOURCE_MISSING: {"source": "a.json"},
        AnomalyCode.SOURCE_UNREADABLE: {"source": "a.json", "reason": UnreadableReason.LOCKED},
        AnomalyCode.PARSE_ERROR: {"source": "a.json"},
        AnomalyCode.UNEXPECTED_VALUE: {
            "source": "a.json",
            "field": "state",
            "received": HIDDEN,
            "expected": ("a", "b"),
        },
        AnomalyCode.SCHEMA_CHANGED: {"source": "a.json", "field": "<hidden>"},
        AnomalyCode.IMPLAUSIBLE_VALUE: {"source": "a.json", "field": "n", "received": "-1"},
        AnomalyCode.SENSOR_ERROR: {
            "error": ErrorSummary("KeyError", (Call("stetho/x.py", 3, "<module>"),))
        },
        AnomalyCode.SENSOR_TIMEOUT: {},
        AnomalyCode.ACTION_FAILED: action | {"exit_code": -9, "target": "abc"},
        AnomalyCode.ACTION_TIMEOUT: action | {"timeout_seconds": 30},
        AnomalyCode.ACTION_UNEXPECTED_OUTPUT: action,
        AnomalyCode.ACTION_UNVERIFIED: action,
        AnomalyCode.PROXY_DENIED: action,
    }
    assert set(fields) == set(AnomalyCode)
    return [Anomaly(code=code, module="every", **values) for code, values in fields.items()]


def full_report() -> ProbeReport:
    """Two sensors with items, details, actions and every anomaly."""
    item = Item(
        id="job 1/a",
        label="Job",
        status=Status.CRITICAL,
        summary="Failing",
        advice="Look",
        details=(Detail(label="Last error", value=""),),
        actions=("RERUN_JOB",),
    )
    rich = result(
        "every",
        Status.CRITICAL,
        advice="Do this",
        items=(item,),
        details=(Detail(label="a", value="b"),),
        actions=("RESTART_GATEWAY",),
        anomalies=tuple(AnomalyCode),
    )
    sensors = [
        FakeSensor("calm", result("calm", Status.OK)),
        FakeSensor("every", rich, *every_anomaly()),
        FakeSensor("broken", None),
    ]
    return run_probe(config(), sensors)


def test_every_field_and_every_anomaly_follow_the_contract() -> None:
    api = Api()
    api.store.publish(full_report())

    status, anomalies = check_every_read(api)

    assert status["summary"] == "Title every: Summary every"
    found = {item["code"]: item for item in anomalies["anomalies"] if item["module"] == "every"}
    assert set(found) == {code.value for code in AnomalyCode}
    hidden = found["unexpected_value"]
    assert (hidden["received"], hidden["received_hidden"]) == (None, True)
    assert hidden["expected"] == ["a", "b"]
    shown = found["implausible_value"]
    assert (shown["received"], shown["received_hidden"]) == ("-1", False)
    assert found["source_unreadable"]["reason"] == "locked"
    assert found["sensor_error"]["error"] == "KeyError · stetho/x.py:3 (<module>)"
    failed = found["action_failed"]
    assert (failed["exit_code"], failed["target"], failed["timeout_seconds"]) == (-9, "abc", None)
    assert found["action_timeout"]["timeout_seconds"] == 30
    broken = status["sensors"][2]
    assert broken["advice"] == "Read the STETHO log, which gives the type and place of the error."
    assert MARKER not in json.dumps([status, anomalies])


@pytest.mark.parametrize(
    ("interval", "timeout", "expected"),
    [(30, 5, 90), (10, 5, 30), (10, 60, 70), (1, 86400, 86401), (86400, 1, 259200)],
)
def test_delay_before_a_report_is_stale(interval: int, timeout: int, expected: int) -> None:
    assert stale_after_seconds(ProbeSettings(interval, timeout)) == expected


def test_old_report_never_looks_healthy_nor_offers_anything() -> None:
    api = Api()
    api.store.publish(full_report())
    api.time.seconds += 90

    fresh, _ = check_every_read(api)
    api.time.seconds += 0.001
    status, anomalies = check_every_read(api)

    assert (fresh["stale"], fresh["status"]) == (False, "critical")
    outdated = "The data is out of date"
    assert (status["status"], status["summary"], status["stale"]) == ("unknown", outdated, True)
    for sensor in status["sensors"]:
        assert (sensor["status"], sensor["summary"], sensor["stale"]) == (
            "unknown",
            outdated,
            True,
        )
        assert (sensor["advice"], sensor["details"], sensor["actions"]) == (None, [], [])
        for entry in sensor["items"]:
            assert (entry["status"], entry["summary"]) == ("unknown", outdated)
            assert (entry["advice"], entry["details"], entry["actions"]) == (None, [], [])
    every = status["sensors"][1]
    assert every["title"] == "Title every"
    assert every["items"][0]["label"] == "Job"
    assert len(every["anomalies"]) == len(AnomalyCode)
    assert anomalies["stale"]
    assert len(anomalies["anomalies"]) == status["anomaly_count"] > len(AnomalyCode)
    assert status["checked_at"] == fresh["checked_at"]


def test_new_report_ends_the_staleness() -> None:
    api = Api()
    report = run_probe(api.config)
    api.store.publish(report)
    api.time.seconds += 1000
    api.store.publish(report)

    status, _ = check_every_read(api)

    assert (status["status"], status["stale"]) == ("ok", False)


def test_report_without_any_sensor_that_counts_is_unknown() -> None:
    translator = load_translator(Language.EN)
    only_info = run_probe(config(), [FakeSensor("info", result("info", Status.INFO))])

    for report in (only_info, run_probe(config(), [])):
        current = view(report, translator, stale=False)

        assert (current.status, current.summary) == (Status.UNKNOWN, "Nothing could be checked")


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("module=every", 13),
        ("module=broken", 1),
        ("module=absent", 0),
        ("code=sensor_error", 2),
        ("code=sensor_timeout&module=every", 1),
        ("code=proxy_denied&module=broken", 0),
    ],
)
def test_anomalies_are_filtered(query: str, expected: int) -> None:
    api = Api()
    api.store.publish(full_report())

    answer = conforming(api, "/api/v1/anomalies", query)

    assert len(answer["anomalies"]) == expected


@pytest.mark.parametrize(
    "query",
    [
        "code=nope",
        "code=",
        "module=Gate-way",
        "module=",
        "module=a%0A",
        "modul=gateway",
        "since=2026-01-01",
        "active_only=true",
        "module=a&module=a",
        "code=parse_error&code=parse_error",
        "x",
    ],
)
def test_bad_filter_is_refused_and_not_ignored(query: str) -> None:
    api = Api()

    answer = api.get("/api/v1/anomalies", query)

    assert (answer.status, answer.json["code"]) == (400, "invalid_request")
    assert errors_against_contract(answer.json, "/api/v1/anomalies", 400) == []


SERVED = {
    path: path.replace("{sensor_id}", "gateway")
    for path, operations in CONTRACT["paths"].items()
    if "get" in operations and operations["get"].get("x-stetho-served", True)
}


@pytest.mark.parametrize("template", sorted(SERVED))
def test_every_route_marked_served_answers(template: str) -> None:
    api = Api()
    api.store.publish(run_probe(api.config))

    assert api.get(SERVED[template]).status == 200


@pytest.mark.parametrize("template", sorted(SERVED))
def test_every_served_route_refuses_unknown_parameters(template: str) -> None:
    api = Api()
    api.store.publish(run_probe(api.config))

    answer = api.get(SERVED[template], f"{MARKER}=1")

    assert (answer.status, answer.json["code"]) == (400, "invalid_request")
    assert MARKER not in answer.body.decode()


@pytest.mark.parametrize("template", sorted(set(SERVED) - {"/health"}))
def test_every_served_route_needs_the_password(template: str) -> None:
    answer = Api().get(SERVED[template], authenticated=False)

    assert answer.status == 401


@pytest.mark.parametrize(
    "path",
    ["/api/v1/csrf-token", "/api/v1/actions", "/api/v1/prescriptions/abc", "/api/v1/sessions"],
)
def test_route_served_later_is_absent(path: str) -> None:
    answer = Api().get(path)

    assert (answer.status, answer.json["code"]) == (404, "not_found")


def test_nothing_can_be_submitted() -> None:
    assert Api().get("/api/v1/prescriptions", method="POST").status == 405


@pytest.mark.parametrize(
    "identifier", ["absent", "Gateway", "gateway%0A", "gateway\n", "..", "a" * 65, "gate way"]
)
def test_unknown_or_malformed_sensor_is_not_found(identifier: str) -> None:
    api = Api()
    api.store.publish(run_probe(api.config))

    answer = api.get(f"/api/v1/sensors/{identifier}")

    assert (answer.status, answer.json["code"]) == (404, "not_found")
    template = "/api/v1/sensors/{sensor_id}"
    assert errors_against_contract(answer.json, template, 404) == []


def test_sensor_is_not_found_before_the_first_pass() -> None:
    assert Api().get("/api/v1/sensors/gateway").status == 404


def test_requests_read_the_report_and_run_no_sensor(monkeypatch: pytest.MonkeyPatch) -> None:
    api = Api()

    def never(*_arguments: object) -> None:
        pytest.fail("a request ran the probe")

    monkeypatch.setattr("stetho.probe.Probe.run_once", never)

    for path in SERVED.values():
        api.get(path)


def test_contract_is_served_as_json() -> None:
    answer = Api().get("/api/v1/openapi.json")

    assert answer.json == CONTRACT


@pytest.mark.parametrize(
    ("language", "title", "later"),
    [("en", "STETHO API", "Not served yet"), ("fr", "API de STETHO", "Pas encore servie")],
)
def test_documentation_lists_every_route(language: str, title: str, later: str) -> None:
    answer = Api(config(language=language)).get("/api/docs")

    page = answer.body.decode()
    assert answer.content_type.startswith("text/html")
    assert f'<html lang="{language}">' in page
    assert f"<h1>{title}</h1>" in page
    for path, operations in CONTRACT["paths"].items():
        for method in operations:
            assert f"<code>{method.upper()} {path}</code>" in page
    assert page.count(later) == 4
    for forbidden in ("<script", "style=", "<style", "http://", "https://", "<link", "<form"):
        assert forbidden not in page


def test_documentation_escapes_the_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    changed = json.loads(json.dumps(CONTRACT))
    changed["paths"]["/health"]["get"]["summary"] = "<script>alert(1)</script>"
    monkeypatch.setattr("stetho.web.routes.load_yaml", lambda _path: changed)

    page = Api().get("/api/docs").body.decode()

    assert "<script>" not in page
    assert "&lt;script&gt;" in page


def test_head_is_accepted_on_reading_routes() -> None:
    assert Api().get("/api/v1/status", method="HEAD").status == 200


def test_report_time_does_not_depend_on_the_date_of_the_server() -> None:
    api = Api()
    old = run_probe(api.config)
    # Dated long ago by the wall clock, but just received.
    api.store.publish(
        ProbeReport(old.status, NOW - timedelta(days=400), old.sensors, old.anomalies)
    )

    assert conforming(api, "/api/v1/status")["stale"] is False


def test_register_entries_keep_their_times() -> None:
    register = AnomalyRegister()
    anomaly = every_anomaly()[0]
    register.record(anomaly, NOW)
    register.record(anomaly, NOW + timedelta(seconds=30))
    api = Api()
    api.store.publish(ProbeReport(Status.UNKNOWN, NOW, (), register.entries()))

    (entry,) = conforming(api, "/api/v1/anomalies")["anomalies"]

    assert (entry["first_seen"], entry["last_seen"], entry["count"]) == (
        "2026-01-15T12:00:00+00:00",
        "2026-01-15T12:00:30+00:00",
        2,
    )


def test_unreadable_contract_stops_the_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("stetho.web.routes.load_yaml", lambda _path: [])

    with pytest.raises(TypeError, match="must be a mapping"):
        Api()
