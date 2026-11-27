"""Checks of the hand-written contract itself. The responses are checked against it elsewhere."""

from pathlib import Path
from typing import Any

import pytest
from openapi_spec_validator import validate

from stetho.anomalies import AnomalyCode
from stetho.patterns import ACTION_CODE, ACTION_TARGET, SENSOR_ID
from stetho.results import Status
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
    assert all(method == "GET" for method, _ in SERVED)


@pytest.mark.parametrize("key", sorted(OPERATIONS))
def test_every_route_but_health_requires_the_password(key: tuple[str, str]) -> None:
    operation = OPERATIONS[key]
    if key == ("GET", "/health"):
        assert operation["security"] == []
    else:
        assert "security" not in operation  # the global one applies
        assert "401" in operation["responses"]
    assert CONTRACT["security"] == [{"basicAuth": []}]


@pytest.mark.parametrize("key", sorted(OPERATIONS))
def test_every_route_describes_the_common_errors(key: tuple[str, str]) -> None:
    # 400 includes a host name that is not allowed.
    assert {"400", "405", "500"} <= set(OPERATIONS[key]["responses"])


def test_every_error_response_uses_the_same_format() -> None:
    for response in CONTRACT["components"]["responses"].values():
        schema = response["content"]["application/json"]["schema"]
        assert schema == {"$ref": "#/components/schemas/Error"}
    for operation in OPERATIONS.values():
        for code, response in operation["responses"].items():
            if code >= "400":
                assert response["$ref"].startswith("#/components/responses/")


def test_prescription_needs_a_csrf_token() -> None:
    operation = OPERATIONS["POST", "/api/v1/prescriptions"]
    (header,) = operation["parameters"]

    assert (header["name"], header["in"], header["required"]) == ("X-CSRF-Token", "header", True)
    assert {"403", "415"} <= set(operation["responses"])


def test_enumerations_match_the_code() -> None:
    assert SCHEMAS["Status"]["enum"] == [status.value for status in Status]
    assert SCHEMAS["AnomalyCode"]["enum"] == [code.value for code in AnomalyCode]


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


def test_example_server_is_not_a_real_one() -> None:
    (server,) = CONTRACT["servers"]

    assert server["variables"]["host"]["default"] == "example.com"
