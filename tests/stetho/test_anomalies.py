from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from stetho.anomalies import (
    HIDDEN,
    Anomaly,
    AnomalyCode,
    AnomalyRegister,
    UnreadableReason,
    describe,
    received_value,
)
from stetho.config import Language
from stetho.errors import ErrorSummary
from stetho.i18n import load_translator

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
SOURCE = "gateway_state.json"
ACTION: dict[str, Any] = {"action": "RERUN_JOB"}

# The smallest valid fields of each code.
VALID: dict[AnomalyCode, dict[str, Any]] = {
    AnomalyCode.SOURCE_MISSING: {"source": SOURCE},
    AnomalyCode.SOURCE_UNREADABLE: {"source": SOURCE, "reason": UnreadableReason.LOCKED},
    AnomalyCode.PARSE_ERROR: {"source": SOURCE},
    AnomalyCode.UNEXPECTED_VALUE: {
        "source": SOURCE,
        "field": "gateway_state",
        "received": "napping",
        "expected": ("running", "stopped"),
    },
    AnomalyCode.SCHEMA_CHANGED: {"source": SOURCE, "field": "updated_at"},
    AnomalyCode.IMPLAUSIBLE_VALUE: {"source": SOURCE, "field": "active_agents", "received": "-1"},
    AnomalyCode.SENSOR_ERROR: {"error": ErrorSummary("KeyError", ())},
    AnomalyCode.SENSOR_TIMEOUT: {},
    AnomalyCode.ACTION_FAILED: ACTION | {"exit_code": 0},
    AnomalyCode.ACTION_TIMEOUT: ACTION | {"timeout_seconds": 30},
    AnomalyCode.ACTION_UNEXPECTED_OUTPUT: ACTION,
    AnomalyCode.ACTION_UNVERIFIED: ACTION,
    AnomalyCode.PROXY_DENIED: ACTION,
}
ACTION_CODES = [code for code, values in VALID.items() if "action" in values]


def anomaly(code: AnomalyCode = AnomalyCode.PARSE_ERROR, **changes: Any) -> Anomaly:
    values: dict[str, Any] = {"module": "gateway"} | VALID[code] | changes
    return Anomaly(code=code, **values)


def test_the_codes_are_the_documented_ones() -> None:
    assert set(VALID) == set(AnomalyCode)
    assert len(AnomalyCode) == 13


@pytest.mark.parametrize("language", list(Language))
@pytest.mark.parametrize("code", list(AnomalyCode))
def test_every_anomaly_has_a_complete_sentence(language: Language, code: AnomalyCode) -> None:
    text = describe(anomaly(code), load_translator(language))

    assert text.endswith((".", "…"))
    assert "{" not in text
    assert "None" not in text


@pytest.mark.parametrize("language", list(Language))
@pytest.mark.parametrize("code", ACTION_CODES)
def test_action_sentence_names_the_target_when_there_is_one(
    language: Language, code: AnomalyCode
) -> None:
    translator = load_translator(language)

    assert "3f9c2a1b7e40" in describe(anomaly(code, target="3f9c2a1b7e40"), translator)


@pytest.mark.parametrize("language", list(Language))
@pytest.mark.parametrize("reason", list(UnreadableReason))
def test_every_unreadable_reason_is_translated(
    language: Language, reason: UnreadableReason
) -> None:
    translator = load_translator(language)
    text = describe(anomaly(AnomalyCode.SOURCE_UNREADABLE, reason=reason), translator)

    assert translator.text(f"anomaly.reason.{reason}") in text


def test_sentences_hold_the_details() -> None:
    translator = load_translator(Language.EN)

    assert describe(anomaly(AnomalyCode.UNEXPECTED_VALUE), translator) == (
        "gateway_state is “napping”, a value STETHO does not know. Expected: running, stopped."
    )
    assert describe(anomaly(AnomalyCode.ACTION_FAILED), translator) == (
        "The action RERUN_JOB failed (exit code 0)."
    )


def test_hidden_value_is_shown_as_such() -> None:
    item = anomaly(AnomalyCode.IMPLAUSIBLE_VALUE, received=HIDDEN)

    assert "valeur masquée" in describe(item, load_translator(Language.FR))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("paused", "paused"),
        ("2026-11-27T12:00:00+00:00", "2026-11-27T12:00:00+00:00"),
        ("a" * 64, "a" * 64),
        (-1, "-1"),
        (2.5, "2.5"),
        (float("nan"), "nan"),
        (True, "true"),
        (False, "false"),
        (None, "null"),
    ],
)
def test_short_simple_value_is_kept(raw: object, expected: str) -> None:
    assert received_value(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "a" * 65,
        "two words",
        "line\n",
        "a/b",
        "key=value",
        "user@example.invalid",
        "é",
        10**70,
        10**100_000,
        ["x"],
        {"x": 1},
        b"x",
    ],
    ids=lambda raw: type(raw).__name__,  # pytest cannot name a huge integer
)
def test_other_value_is_hidden(raw: object) -> None:
    assert received_value(raw) is HIDDEN


@pytest.mark.parametrize("code", list(AnomalyCode))
def test_each_required_field_is_required(code: AnomalyCode) -> None:
    for name in VALID[code]:
        values = {key: value for key, value in VALID[code].items() if key != name}
        with pytest.raises(ValueError, match=f"missing {name}"):
            Anomaly(code=code, module="gateway", **values)


@pytest.mark.parametrize(
    "code",
    [
        code
        for code in AnomalyCode
        if code not in (AnomalyCode.UNEXPECTED_VALUE, AnomalyCode.IMPLAUSIBLE_VALUE)
    ],
)
def test_received_value_is_kept_for_two_codes_only(code: AnomalyCode) -> None:
    for value in ("x", HIDDEN):
        with pytest.raises(ValueError, match="does not take received"):
            anomaly(code, received=value)


def test_target_is_only_for_actions() -> None:
    with pytest.raises(ValueError, match="does not take target"):
        anomaly(AnomalyCode.PARSE_ERROR, target="abc")


@pytest.mark.parametrize(
    ("code", "changes"),
    [
        (AnomalyCode.PARSE_ERROR, {"module": "../x"}),
        (AnomalyCode.PARSE_ERROR, {"source": "/srv/made-up/state.json"}),
        (AnomalyCode.PARSE_ERROR, {"source": " "}),
        (AnomalyCode.SCHEMA_CHANGED, {"field": " "}),
        (AnomalyCode.UNEXPECTED_VALUE, {"received": "a private message"}),
        (AnomalyCode.PROXY_DENIED, {"action": "rm -rf"}),
        (AnomalyCode.PROXY_DENIED, {"target": "a; b"}),
    ],
)
def test_invalid_anomaly_is_refused_without_quoting(
    code: AnomalyCode, changes: dict[str, str]
) -> None:
    with pytest.raises(ValueError, match=r".+") as caught:
        anomaly(code, **changes)

    assert not any(value.strip() and value in str(caught.value) for value in changes.values())


def test_identical_anomalies_are_grouped() -> None:
    register = AnomalyRegister()
    later = NOW + timedelta(seconds=30)

    first = register.record(anomaly(AnomalyCode.UNEXPECTED_VALUE), NOW)
    second = register.record(anomaly(AnomalyCode.UNEXPECTED_VALUE, received="resting"), later)

    assert register.entries() == (second,)
    assert (second.id, second.count) == (first.id, 2)
    assert (second.first_seen, second.last_seen) == (NOW, later)
    assert second.anomaly.received == "resting"


@pytest.mark.parametrize(
    ("code", "changes"),
    [
        (AnomalyCode.SCHEMA_CHANGED, {"module": "platforms"}),
        (AnomalyCode.SCHEMA_CHANGED, {"source": "cron/jobs.json"}),
        (AnomalyCode.SCHEMA_CHANGED, {"field": "other"}),
        (AnomalyCode.ACTION_UNVERIFIED, {"action": "PAUSE_JOB"}),
        (AnomalyCode.ACTION_UNVERIFIED, {"target": "other"}),
    ],
)
def test_different_anomalies_stay_apart(code: AnomalyCode, changes: dict[str, str]) -> None:
    register = AnomalyRegister()

    first = register.record(anomaly(code), NOW)
    second = register.record(anomaly(code, **changes), NOW)

    assert register.entries() == (first, second)
    assert first.id != second.id


def test_another_code_on_the_same_source_stays_apart() -> None:
    register = AnomalyRegister()

    register.record(anomaly(AnomalyCode.PARSE_ERROR), NOW)
    register.record(anomaly(AnomalyCode.SOURCE_MISSING), NOW)

    assert len(register.entries()) == 2


def test_identifier_does_not_change_between_registers() -> None:
    first = AnomalyRegister().record(anomaly(), NOW)
    second = AnomalyRegister().record(anomaly(), NOW)

    assert first.id == second.id


def test_time_without_zone_is_refused() -> None:
    with pytest.raises(ValueError, match="time zone"):
        AnomalyRegister().record(anomaly(), NOW.replace(tzinfo=None))
