from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from stetho.anomalies import Anomaly, AnomalyCode, UnreadableReason
from stetho.config import GATEWAY_THRESHOLDS, Language
from stetho.i18n import load_translator
from stetho.modules.gateway.reader import GatewayReading, GatewayState, Heartbeat, load_adapter
from stetho.modules.gateway.rules import RulesError, Verdict, evaluate, load_rules
from stetho.results import Status

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
RULES = load_rules()
RULES_FILE = Path(__file__).parents[2] / "stetho" / "modules" / "gateway" / "rules.yaml"
ADAPTER = load_adapter()
RESTART_GATEWAY = ("RESTART_GATEWAY",)
RESTART_CONTAINER = ("RESTART_CONTAINER",)


def reading(
    *,
    age: float | None = 30,
    command: bool = False,
    anomalies: tuple[Anomaly, ...] = (),
    **changes: Any,
) -> GatewayReading:
    """A gateway whose last sign of life is `age` seconds old."""
    heartbeat = None if age is None else Heartbeat(NOW - timedelta(seconds=age), command)
    values: dict[str, Any] = {
        "state": "running",
        "heartbeat": heartbeat,
        "exit_reason": None,
        "has_other_exit_reason": False,
        "desired_state": None,
        "desired_state_unrecognised": False,
        "active_agents": 0,
        "served_profiles": (),
        "hermes_version": None,
    }
    return GatewayReading("default", GatewayState(**(values | changes)), None, anomalies)


def judge(item: GatewayReading, missing_since: datetime | None = None) -> Verdict:
    return evaluate(item, RULES, now=NOW, missing_since=missing_since)


def found(status: Status, summary: str, actions: tuple[str, ...] = ()) -> Verdict:
    return Verdict(status, summary, actions)


def unexpected(field: str = "desired_state") -> tuple[Anomaly, ...]:
    return (
        Anomaly(
            code=AnomalyCode.UNEXPECTED_VALUE,
            module="gateway",
            source="gateway_state.json",
            field=field,
            received="new",
            expected=("a",),
        ),
    )


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (0, found(Status.OK, "online")),
        (-200, found(Status.OK, "online")),  # a clock slightly ahead
        (120, found(Status.OK, "online")),
        (121, found(Status.WARN, "late")),
        (300, found(Status.WARN, "late")),
        (301, found(Status.CRITICAL, "down", RESTART_CONTAINER)),
    ],
)
def test_age_of_the_sign_of_life(age: int, expected: Verdict) -> None:
    assert judge(reading(age=age)) == expected


@pytest.mark.parametrize("age", [30, 3600, None])
@pytest.mark.parametrize(
    ("changes", "summary"),
    [
        ({"state": "stopped"}, "stopped"),
        ({"state": "startup_failed"}, "startup_failed"),
        ({"state": "degraded", "exit_reason": "loop_liveness_watchdog"}, "watchdog"),
        ({"state": "degraded", "exit_reason": "shutdown_watchdog"}, "watchdog"),
    ],
)
def test_stopped_gateway_is_not_judged_by_its_age(
    age: int | None, changes: dict[str, Any], summary: str
) -> None:
    assert judge(reading(age=age, **changes)) == found(Status.CRITICAL, summary, RESTART_GATEWAY)


def test_stopped_by_a_command_is_still_stopped() -> None:
    assert judge(reading(state="stopped", command=True)).summary == "stopped"


def test_deliberate_stop_offers_no_action() -> None:
    item = reading(state="stopped", desired_state="stopped", age=3600)

    assert judge(item) == found(Status.CRITICAL, "deliberate_stop")


@pytest.mark.parametrize(
    "changes",
    [
        {"state": "startup_failed"},
        {"state": "degraded"},
        {"state": "degraded", "exit_reason": "shutdown_watchdog"},
        {"state": "running", "age": 3600},
        {"state": "stopped"},
    ],
)
@pytest.mark.parametrize(
    "intent", [{"desired_state": "stopped"}, {"desired_state_unrecognised": True}]
)
def test_no_repair_against_or_without_a_known_intent(
    changes: dict[str, Any], intent: dict[str, Any]
) -> None:
    assert judge(reading(**changes, **intent)).actions == ()


def test_intent_to_run_keeps_the_repair() -> None:
    assert judge(reading(state="stopped", desired_state="running")).actions == RESTART_GATEWAY


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (5, found(Status.UNKNOWN, "command_waiting")),
        (120, found(Status.UNKNOWN, "command_waiting")),
        (121, found(Status.WARN, "command_waiting")),
        (301, found(Status.CRITICAL, "command_down", RESTART_CONTAINER)),
    ],
)
@pytest.mark.parametrize("state", ["running", "degraded", "starting", "draining"])
def test_date_written_by_a_command_never_looks_healthy(
    state: str, age: int, expected: Verdict
) -> None:
    assert judge(reading(state=state, age=age, command=True)) == expected


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("degraded", found(Status.WARN, "degraded", RESTART_GATEWAY)),
        ("starting", found(Status.WARN, "starting")),
        ("draining", found(Status.WARN, "draining")),
        (None, found(Status.WARN, "unknown_state")),
    ],
)
def test_other_states_with_a_recent_sign_of_life(state: str | None, expected: Verdict) -> None:
    assert judge(reading(state=state)) == expected


@pytest.mark.parametrize("state", ADAPTER.gateway_states)
def test_every_state_of_the_adapter_has_its_own_rule(state: str) -> None:
    assert judge(reading(state=state)).summary != "unknown_state"


def test_rules_use_only_values_of_the_adapter() -> None:
    for rule in RULES.rules:
        assert set(rule.when.state) <= set(ADAPTER.gateway_states)
        assert set(rule.when.exit_reason) <= set(ADAPTER.known_exit_reasons)
        assert rule.when.desired_state in (None, *ADAPTER.desired_states)


@pytest.mark.parametrize("state", ["running", "degraded", None])
def test_without_a_usable_date_nothing_is_healthy(state: str | None) -> None:
    assert judge(reading(state=state, age=None)) == found(Status.UNKNOWN, "no_date")


def test_unknown_value_makes_a_healthy_gateway_a_warning() -> None:
    assert judge(reading(anomalies=unexpected())) == found(Status.WARN, "online_unknown_value")


@pytest.mark.parametrize(
    ("changes", "summary"),
    [({"age": None}, "no_date"), ({"command": True, "age": 5}, "command_waiting")],
)
def test_unknown_value_is_at_least_a_warning(changes: dict[str, Any], summary: str) -> None:
    verdict = judge(reading(anomalies=unexpected("gateway_state"), **changes))

    assert (verdict.status, verdict.summary) == (Status.WARN, summary)


def test_unknown_value_never_lowers_a_status() -> None:
    verdict = judge(reading(age=3600, anomalies=unexpected()))

    assert (verdict.status, verdict.summary) == (Status.CRITICAL, "down")


def test_other_anomalies_do_not_change_the_status() -> None:
    anomaly = Anomaly(
        code=AnomalyCode.SCHEMA_CHANGED, module="gateway", source="x.json", field="new"
    )

    assert judge(reading(anomalies=(anomaly,))).status is Status.OK


def without_state(code: AnomalyCode, source: str = "gateway_state.json") -> GatewayReading:
    reason = UnreadableReason.OTHER if code is AnomalyCode.SOURCE_UNREADABLE else None
    field = "updated_at" if code is AnomalyCode.SCHEMA_CHANGED else None
    anomaly = Anomaly(code=code, module="gateway", source=source, reason=reason, field=field)
    return GatewayReading("default", None, None, (anomaly,))


@pytest.mark.parametrize(
    ("since", "expected"),
    [
        (None, found(Status.UNKNOWN, "missing")),
        (300, found(Status.UNKNOWN, "missing")),
        (301, found(Status.CRITICAL, "missing_too_long", RESTART_CONTAINER)),
    ],
)
def test_missing_state_file(since: int | None, expected: Verdict) -> None:
    missing_since = None if since is None else NOW - timedelta(seconds=since)

    assert judge(without_state(AnomalyCode.SOURCE_MISSING), missing_since) == expected


@pytest.mark.parametrize(
    "code",
    [AnomalyCode.SOURCE_UNREADABLE, AnomalyCode.PARSE_ERROR, AnomalyCode.SCHEMA_CHANGED],
)
def test_unusable_state_file_stays_unknown(code: AnomalyCode) -> None:
    long_ago = NOW - timedelta(days=1)

    assert judge(without_state(code), long_ago) == found(Status.UNKNOWN, "unusable")


def test_only_the_state_file_counts_as_missing() -> None:
    item = without_state(AnomalyCode.SOURCE_MISSING, "logs/gateway-exit-diag.log")

    assert judge(item).summary == "unusable"


def test_overrides_change_the_thresholds() -> None:
    rules = load_rules({"late_after_minutes": 10, "down_after_minutes": 20})

    verdict = evaluate(reading(age=15 * 60), rules, now=NOW, missing_since=None)

    assert verdict.status is Status.WARN
    assert rules.thresholds["missing_critical_after_minutes"] == 5
    assert RULES.thresholds["down_after_minutes"] == 5


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"late_after_minutes": 5}, "must be lower than"),
        ({"down_after_minutes": 1}, "must be lower than"),
        ({"other": 1}, "unknown threshold: other"),
    ],
)
def test_incoherent_overrides_are_refused(overrides: dict[str, int], message: str) -> None:
    with pytest.raises(RulesError, match=message):
        load_rules(overrides)


def test_settings_allow_exactly_the_thresholds_of_the_rules() -> None:
    assert set(GATEWAY_THRESHOLDS) == set(RULES.thresholds)


@pytest.mark.parametrize("language", list(Language))
def test_every_summary_of_the_rules_has_a_text(language: Language) -> None:
    translator = load_translator(language)
    verdicts = [rule.verdict for rule in RULES.rules] + list(RULES.without_state.values())

    for summary in [v.summary for v in verdicts] + ["online_unknown_value"]:
        assert translator.placeholders(f"gateway.summary.{summary}") <= {"age"}
    for reason in ADAPTER.known_exit_reasons:
        assert translator.text(f"gateway.exit_reason.{reason}")


# The advice of each case: none only when everything is fine.
@pytest.mark.parametrize(
    ("changes", "advice"),
    [
        ({}, None),
        ({"state": "stopped"}, "start_again"),
        ({"state": "stopped", "desired_state": "stopped"}, "deliberate_stop"),
        ({"state": "startup_failed"}, "fix_then_start"),
        ({"state": "degraded", "exit_reason": "shutdown_watchdog"}, "fix_then_start"),
        ({"command": True, "age": 5}, "wait"),
        ({"command": True, "age": 150}, "wait"),
        ({"command": True, "age": 3600}, "check_container"),
        ({"age": 3600}, "check_container"),
        ({"age": 150}, "wait_and_check_load"),
        ({"age": None}, "check_clock"),
        ({"state": "degraded"}, "check_platforms"),
        ({"state": "starting"}, "wait"),
        ({"state": "draining"}, "wait"),
        ({"state": None}, "check_version"),
    ],
)
def test_advice_of_each_case(changes: dict[str, Any], advice: str | None) -> None:
    assert judge(reading(**changes)).advice == advice


@pytest.mark.parametrize(
    "changes",
    [{"state": "startup_failed"}, {"state": "degraded"}, {"age": 3600}, {"age": 150}],
)
def test_no_advice_to_start_what_was_stopped_on_purpose(changes: dict[str, Any]) -> None:
    assert judge(reading(desired_state="stopped", **changes)).advice == "deliberate_stop"


@pytest.mark.parametrize(
    "changes",
    [
        {"state": "stopped"},
        {"state": "startup_failed"},
        {"state": "degraded", "exit_reason": "shutdown_watchdog"},
        {"age": 3600},
        {"age": 3600, "command": True},
    ],
)
def test_no_advice_to_start_when_the_intent_cannot_be_read(changes: dict[str, Any]) -> None:
    assert judge(reading(desired_state_unrecognised=True, **changes)).advice == "check_intent"


@pytest.mark.parametrize(
    ("changes", "advice"),
    [({}, None), ({"state": "degraded"}, "check_platforms"), ({"age": 150}, "wait_and_check_load")],
)
def test_other_advice_stays_when_the_intent_cannot_be_read(
    changes: dict[str, Any], advice: str | None
) -> None:
    assert judge(reading(desired_state_unrecognised=True, **changes)).advice == advice


def test_healthy_gateway_stopped_on_purpose_has_no_advice() -> None:
    assert judge(reading(desired_state="stopped")).advice is None


@pytest.mark.parametrize(
    ("changes", "advice"),
    [({}, "check_version"), ({"age": None}, "check_clock"), ({"age": 3600}, "check_container")],
)
def test_advice_with_an_unknown_value(changes: dict[str, Any], advice: str) -> None:
    assert judge(reading(anomalies=unexpected(), **changes)).advice == advice


@pytest.mark.parametrize(
    ("code", "advice"),
    [
        (AnomalyCode.SOURCE_MISSING, "check_running_and_mount"),
        (AnomalyCode.PARSE_ERROR, "check_version_and_access"),
    ],
)
def test_advice_without_a_usable_state(code: AnomalyCode, advice: str) -> None:
    assert judge(without_state(code)).advice == advice


@pytest.mark.parametrize("language", list(Language))
def test_every_advice_has_a_text(language: Language) -> None:
    translator = load_translator(language)
    verdicts = [rule.verdict for rule in RULES.rules] + list(RULES.without_state.values())
    keys = {v.advice for v in verdicts if v.advice}

    others = {"check_intent", "check_profile_list", "check_profiles_access"}
    for key in keys | others | {"check_profile_names", "restarts_all"}:
        assert translator.text(f"gateway.advice.{key}")
    assert translator.placeholders("gateway.advice.of_profile") == {"name", "advice"}


def break_rules(document: dict[str, Any], path: str, value: object) -> None:
    *parents, key = path.split(".")
    target: Any = document
    for parent in parents:
        target = target[int(parent)] if parent.isdigit() else target[parent]
    target[key] = value


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        ("extra", 1, "expected thresholds, rules and without_state"),
        ("thresholds.late_after_minutes", 0, "invalid thresholds"),
        ("rules", [], "rules must be a list"),
        ("rules", ["x"], "a rule needs an id"),
        ("rules.0.other", 1, "unknown key"),
        ("rules.0.when", {"colour": "red"}, "unknown condition"),
        ("rules.0.when", {"state": []}, "state must be a list"),
        ("rules.0.when", {"state": "stopped"}, "state must be a list"),
        ("rules.0.when", {"desired_state": 1}, "desired_state must be a text"),
        ("rules.0.when", {"older_than": "never"}, "unknown threshold"),
        ("rules.0.when", {"no_date": False}, "no_date can only be true"),
        ("rules.0.status", "info", "invalid status"),
        ("rules.0.summary", "", "invalid summary"),
        ("rules.0.advice", "", "invalid advice"),
        ("rules.0.advice", 3, "invalid advice"),
        ("rules.0.actions", ["restart"], "invalid actions"),
        ("rules.14.when", {"state": ["running"]}, "last rule must have no condition"),
        ("without_state", {}, "without_state needs"),
        ("without_state.missing", "x", "invalid without_state"),
    ],
)
def test_invalid_rules_are_refused(tmp_path: Path, path: str, value: object, message: str) -> None:
    document = yaml.safe_load(RULES_FILE.read_text(encoding="utf-8"))
    break_rules(document, path, value)
    (tmp_path / "rules.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(RulesError, match=message):
        load_rules(path=tmp_path / "rules.yaml")
