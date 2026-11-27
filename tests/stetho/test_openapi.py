"""Checks of the hand-written contract itself. The responses are checked against it elsewhere."""

import re
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest
from openapi_spec_validator import validate

from stetho.anomalies import _SAFE_VALUE, Anomaly, AnomalyCode, UnreadableReason
from stetho.patterns import ACTION_CODE, ACTION_TARGET, SENSOR_ID
from stetho.results import Item, SensorResult, Status
from stetho.yamlfile import load_yaml

CONTRACT_FILE = Path(__file__).parents[2] / "stetho" / "openapi.yaml"


def contract() -> dict[str, Any]:
    document = load_yaml(CONTRACT_FILE)
    assert isinstance(document, dict)
    return document


CONTRACT = contract()
SCHEMAS = CONTRACT["components"]["schemas"]
OPERATIONS = {
    (method.upper(), path): operation
    for path, item in CONTRACT["paths"].items()
    for method, operation in item.items()
}
SERVED = {
    ("GET", "/health"),
    ("GET", "/api/v1/status"),
    ("GET", "/api/v1/sensors/{sensor_id}"),
    ("GET", "/api/v1/anomalies"),
    ("GET", "/api/v1/openapi.json"),
    ("GET", "/api/docs"),
}
LATER = {
    ("GET", "/api/v1/csrf-token"),
    ("POST", "/api/v1/prescriptions"),
    ("GET", "/api/v1/prescriptions/{rx_id}"),
    ("GET", "/api/v1/actions"),
}


def test_contract_is_a_valid_openapi_document() -> None:
    validate(CONTRACT)


def test_routes_are_those_decided_for_now_and_later() -> None:
    assert set(OPERATIONS) == SERVED | LATER
    later = {key for key, op in OPERATIONS.items() if op.get("x-stetho-served") is False}
    assert later == LATER


def test_only_reading_routes_are_served() -> None:
    served = [key for key, op in OPERATIONS.items() if op.get("x-stetho-served", True)]

    assert {method for method, _ in served} == {"GET"}


@pytest.mark.parametrize("key", sorted(OPERATIONS))
def test_every_route_but_health_requires_the_password(key: tuple[str, str]) -> None:
    operation = OPERATIONS[key]
    if key == ("GET", "/health"):
        assert operation["security"] == []
    else:
        assert "security" not in operation  # the global one applies
        assert operation["responses"]["401"] == {"$ref": "#/components/responses/Unauthorized"}
    assert CONTRACT["security"] == [{"basicAuth": []}]
    scheme = CONTRACT["components"]["securitySchemes"]["basicAuth"]
    assert (scheme["type"], scheme["scheme"]) == ("http", "basic")


# The response each error code must point to. 400 includes a host name that is not allowed.
ERRORS = {
    "400": ("BadRequest",),
    "401": ("Unauthorized",),
    "403": ("Forbidden",),
    "404": ("NotFound",),
    "405": ("MethodNotAllowed",),
    "413": ("PayloadTooLarge",),
    "415": ("UnsupportedMediaType",),
    "429": ("TooManyRequests",),
    "500": ("InternalError",),
}


@pytest.mark.parametrize("key", sorted(OPERATIONS))
def test_every_route_describes_the_common_errors(key: tuple[str, str]) -> None:
    assert {"400", "405", "500"} <= set(OPERATIONS[key]["responses"])


@pytest.mark.parametrize("key", sorted(OPERATIONS))
def test_each_error_code_points_to_its_response(key: tuple[str, str]) -> None:
    for code, response in OPERATIONS[key]["responses"].items():
        if code >= "400":
            allowed = [{"$ref": f"#/components/responses/{name}"} for name in ERRORS[code]]
            assert response in allowed, code


def test_every_error_response_uses_the_same_format() -> None:
    for response in CONTRACT["components"]["responses"].values():
        schema = response["content"]["application/json"]["schema"]
        assert schema == {"$ref": "#/components/schemas/Error"}


def test_prescription_needs_a_csrf_token() -> None:
    operation = OPERATIONS["POST", "/api/v1/prescriptions"]
    (header,) = operation["parameters"]

    assert (header["name"], header["in"], header["required"]) == ("X-CSRF-Token", "header", True)
    assert header["schema"]["maxLength"] == 256
    assert {"403", "413", "415"} <= set(operation["responses"])
    assert list(operation["requestBody"]["content"]) == ["application/json"]


def test_enumerations_match_the_code() -> None:
    assert SCHEMAS["Status"]["enum"] == [status.value for status in Status]
    assert SCHEMAS["AnomalyCode"]["enum"] == [code.value for code in AnomalyCode]
    assert SCHEMAS["UnreadableReason"]["enum"] == [reason.value for reason in UnreadableReason]


@pytest.mark.parametrize(
    ("name", "pattern"),
    [
        ("SensorId", SENSOR_ID.pattern),
        ("ActionCode", ACTION_CODE.pattern),
        ("ActionTarget", ACTION_TARGET.pattern),
    ],
)
def test_patterns_match_the_code(name: str, pattern: str) -> None:
    assert SCHEMAS[name]["pattern"] == f"^{pattern}$"


def test_objects_are_closed_and_their_required_fields_exist() -> None:
    for name, schema in SCHEMAS.items():
        if schema.get("type") == "object":
            assert schema["additionalProperties"] is False, name
            assert set(schema["required"]) <= set(schema["properties"]), name


def test_responses_and_exchanged_files_require_every_field() -> None:
    # A request is the only object that may leave a field out.
    for name, schema in SCHEMAS.items():
        if schema.get("type") == "object" and name != "PrescriptionRequest":
            assert set(schema["required"]) == set(schema["properties"]), name


def test_request_holds_only_what_reaches_nurse() -> None:
    request = set(SCHEMAS["PrescriptionRequest"]["properties"])

    assert request == {"action", "target"}
    assert request <= set(SCHEMAS["Prescription"]["properties"])


def names(kind: type) -> set[str]:
    return {item.name for item in fields(kind)}


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        # `stale` is added when the result is served.
        ("Sensor", names(SensorResult) | {"stale"}),
        ("Item", names(Item)),
        # The register adds the identifier and the times; the message is the translated text.
        (
            "Anomaly",
            names(Anomaly)
            | {"id", "message", "received_hidden", "first_seen", "last_seen", "count"},
        ),
        ("AnomalyList", {"checked_at", "stale", "anomalies"}),
        (
            "StatusReport",
            {"status", "summary", "checked_at", "stale", "anomaly_count", "sensors"},
        ),
        ("Prescription", {"rx_id", "action", "target", "origin", "requested_by", "created_at"}),
        (
            "PrescriptionResult",
            {
                "rx_id",
                "status",
                "refusal_reason",
                "exit_code",
                "output_excerpt",
                "anomaly",
                "started_at",
                "finished_at",
                "verified",
            },
        ),
    ],
)
def test_objects_hold_exactly_the_decided_fields(schema: str, expected: set[str]) -> None:
    assert set(SCHEMAS[schema]["properties"]) == expected


# The status codes of each route.
READ = {"200", "400", "401", "405", "500"}
CODES = {
    ("GET", "/health"): {"200", "400", "405", "500"},
    ("GET", "/api/v1/status"): READ,
    ("GET", "/api/v1/sensors/{sensor_id}"): READ | {"404"},
    ("GET", "/api/v1/anomalies"): READ,
    ("GET", "/api/v1/openapi.json"): READ,
    ("GET", "/api/docs"): READ,
    ("GET", "/api/v1/csrf-token"): READ,
    ("POST", "/api/v1/prescriptions"): {
        "202",
        "400",
        "401",
        "403",
        "405",
        "413",
        "415",
        "429",
        "500",
    },
    ("GET", "/api/v1/prescriptions/{rx_id}"): READ | {"404"},
    ("GET", "/api/v1/actions"): READ,
}


@pytest.mark.parametrize("key", sorted(OPERATIONS))
def test_each_route_has_exactly_its_status_codes(key: tuple[str, str]) -> None:
    assert set(OPERATIONS[key]["responses"]) == CODES[key]


@pytest.mark.parametrize(
    ("path", "expected"),
    [("/api/v1/anomalies", [("module", "query"), ("code", "query")]), ("/api/v1/status", [])],
)
def test_filters_are_exactly_the_decided_ones(path: str, expected: list[tuple[str, str]]) -> None:
    parameters = OPERATIONS["GET", path].get("parameters", [])

    assert [(item["name"], item["in"]) for item in parameters] == expected


@pytest.mark.parametrize(
    ("response", "header"), [("Unauthorized", "WWW-Authenticate"), ("MethodNotAllowed", "Allow")]
)
def test_error_responses_require_their_header(response: str, header: str) -> None:
    assert CONTRACT["components"]["responses"][response]["headers"][header]["required"] is True


def test_other_enumerations_are_the_decided_ones() -> None:
    assert SCHEMAS["PrescriptionResult"]["properties"]["status"]["enum"] == [
        "queued",
        "done",
        "refused",
        "failed",
        "timeout",
        "unexpected",
        "unverified",
    ]
    assert SCHEMAS["Prescription"]["properties"]["origin"]["enum"] == ["manual", "auto"]
    assert SCHEMAS["Error"]["properties"]["code"]["enum"] == [
        "invalid_request",
        "invalid_host",
        "unauthorized",
        "not_found",
        "method_not_allowed",
        "internal_error",
        "csrf_invalid",
        "cross_site",
        "payload_too_large",
        "unsupported_media_type",
        "bad_target",
        "rate_limited",
    ]


def test_sizes_are_bounded() -> None:
    log = OPERATIONS["GET", "/api/v1/actions"]["responses"]["200"]["content"]

    assert log["application/json"]["schema"]["maxItems"] == 100
    assert SCHEMAS["PrescriptionResult"]["properties"]["output_excerpt"]["maxLength"] == 2048
    assert SCHEMAS["CsrfToken"]["properties"]["token"]["maxLength"] == 256


@pytest.mark.parametrize(
    ("value", "accepted"),
    [
        ("2026-01-15T12:00:00Z-7f3a", True),
        ("a" * 64, True),
        ("a" * 65, False),
        ("", False),
        ("..", False),
        (".hidden", False),
        ("-option", False),
        ("../x", False),
        ("a/b", False),
        ("a\n", False),
    ],
)
def test_identifier_that_may_name_a_file_is_safe(value: str, accepted: bool) -> None:
    # `fullmatch`, as the code must check: a Python validator would accept a final newline.
    assert bool(re.fullmatch(SCHEMAS["PrescriptionId"]["pattern"], value)) is accepted


def test_received_value_is_filtered_as_in_the_code() -> None:
    pattern = SCHEMAS["Anomaly"]["properties"]["received"]["pattern"]

    assert pattern == f"^{_SAFE_VALUE.pattern}$"


@pytest.mark.parametrize(
    ("value", "accepted"),
    [
        ("shared-account", True),
        ("Zoé Martin", True),
        ("a\nb", False),
        ("a\x00", False),
        ("a\x7f", False),
        ("a\x9b", False),
    ],
)
def test_requester_name_has_no_control_character(value: str, accepted: bool) -> None:
    schema = SCHEMAS["Prescription"]["properties"]["requested_by"]

    assert bool(re.fullmatch(schema["pattern"], value)) is accepted
    assert schema["maxLength"] == 64


@pytest.mark.parametrize(
    ("value", "accepted"), [("a" * 128, True), ("a" * 129, False), ("Not a code", False)]
)
def test_refusal_reason_is_a_short_code(value: str, accepted: bool) -> None:
    pattern = SCHEMAS["PrescriptionResult"]["properties"]["refusal_reason"]["pattern"]

    assert bool(re.fullmatch(pattern, value)) is accepted


def test_anomaly_list_says_when_it_was_checked() -> None:
    response = OPERATIONS["GET", "/api/v1/anomalies"]["responses"]["200"]

    assert response["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/AnomalyList"
    }
    assert {"checked_at", "stale"} <= set(SCHEMAS["AnomalyList"]["required"])


def test_example_server_is_not_a_real_one() -> None:
    (server,) = CONTRACT["servers"]

    assert server["url"] == "https://{host}/stetho"
    assert server["variables"]["host"]["default"] == "example.com"
