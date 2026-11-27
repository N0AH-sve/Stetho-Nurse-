"""Applies the rules of `rules.yaml` to what the reader returned."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from types import MappingProxyType

from stetho.anomalies import AnomalyCode
from stetho.modules.gateway.reader import STATE_FILE, GatewayReading, GatewayState
from stetho.patterns import ACTION_CODE
from stetho.results import Status
from stetho.yamlfile import load_yaml

_RULES_FILE = Path(__file__).parent / "rules.yaml"
_CONDITIONS = frozenset(
    {"state", "exit_reason", "desired_state", "written_by_command", "older_than", "no_date"}
)
_WITHOUT_STATE = ("missing", "missing_too_long", "unusable")


class RulesError(Exception):
    """The rules or their thresholds are invalid: STETHO must not start."""


@dataclass(frozen=True)
class Verdict:
    status: Status
    summary: str  # key under gateway.summary
    actions: tuple[str, ...]


@dataclass(frozen=True)
class Conditions:
    """All those that are set must hold. An empty value is not checked."""

    state: tuple[str, ...] = ()
    exit_reason: tuple[str, ...] = ()
    desired_state: str | None = None
    written_by_command: bool = False
    older_than: str | None = None  # name of a threshold
    no_date: bool = False


@dataclass(frozen=True)
class Rule:
    id: str
    when: Conditions
    verdict: Verdict


@dataclass(frozen=True)
class Rules:
    thresholds: Mapping[str, int]  # minutes
    rules: tuple[Rule, ...]
    without_state: Mapping[str, Verdict]

    def threshold(self, name: str) -> timedelta:
        return timedelta(minutes=self.thresholds[name])


def _verdict(entry: Mapping[object, object], where: str) -> Verdict:
    status = entry.get("status")
    summary = entry.get("summary")
    actions = entry.get("actions", [])
    allowed = {item.value for item in Status} - {Status.INFO.value}
    if not isinstance(status, str) or status not in allowed:
        raise RulesError(f"{where}: invalid status")
    if not isinstance(summary, str) or not summary:
        raise RulesError(f"{where}: invalid summary")
    if not isinstance(actions, list) or not all(
        isinstance(code, str) and ACTION_CODE.fullmatch(code) for code in actions
    ):
        raise RulesError(f"{where}: invalid actions")
    return Verdict(Status(status), summary, tuple(actions))


def _conditions(when: object, thresholds: Mapping[str, int], where: str) -> Conditions:
    if not isinstance(when, dict) or when.keys() - _CONDITIONS:
        raise RulesError(f"{where}: unknown condition")
    lists: dict[str, tuple[str, ...]] = {}
    for key in ("state", "exit_reason"):
        values = when.get(key, [])
        if (key in when and not values) or not (
            isinstance(values, list) and all(isinstance(value, str) for value in values)
        ):
            raise RulesError(f"{where}: {key} must be a list of texts")
        lists[key] = tuple(values)
    desired_state = when.get("desired_state")
    if "desired_state" in when and not isinstance(desired_state, str):
        raise RulesError(f"{where}: desired_state must be a text")
    older_than = when.get("older_than")
    if "older_than" in when and older_than not in thresholds:
        raise RulesError(f"{where}: unknown threshold")
    for key in ("written_by_command", "no_date"):
        if key in when and when[key] is not True:
            raise RulesError(f"{where}: {key} can only be true")
    return Conditions(
        state=lists["state"],
        exit_reason=lists["exit_reason"],
        desired_state=desired_state,
        written_by_command="written_by_command" in when,
        older_than=older_than,
        no_date="no_date" in when,
    )


def _rule(entry: object, thresholds: Mapping[str, int]) -> Rule:
    if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
        raise RulesError("a rule needs an id")
    name = entry["id"]
    if entry.keys() - {"id", "when", "status", "summary", "actions"}:
        raise RulesError(f"{name}: unknown key")
    return Rule(name, _conditions(entry.get("when"), thresholds, name), _verdict(entry, name))


def _check_thresholds(thresholds: Mapping[str, int]) -> None:
    if thresholds["late_after_minutes"] >= thresholds["down_after_minutes"]:
        raise RulesError("gateway: late_after_minutes must be lower than down_after_minutes")


def load_rules(overrides: Mapping[str, int] | None = None, path: Path = _RULES_FILE) -> Rules:
    """Load the rules, with the thresholds changed by the installation."""
    document = load_yaml(path)
    if not isinstance(document, dict) or document.keys() != {
        "thresholds",
        "rules",
        "without_state",
    }:
        raise RulesError("expected thresholds, rules and without_state")
    thresholds = document["thresholds"]
    if not isinstance(thresholds, dict) or not all(
        isinstance(value, int) and value > 0 for value in thresholds.values()
    ):
        raise RulesError("invalid thresholds")
    if unknown := set(overrides or {}) - set(thresholds):
        raise RulesError(f"unknown threshold: {', '.join(sorted(unknown))}")
    thresholds = {**thresholds, **(overrides or {})}
    _check_thresholds(thresholds)
    entries = document["rules"]
    if not isinstance(entries, list) or not entries:
        raise RulesError("rules must be a list")
    rules = tuple(_rule(entry, thresholds) for entry in entries)
    if rules[-1].when != Conditions():
        raise RulesError("the last rule must have no condition")
    without = document["without_state"]
    if not isinstance(without, dict) or set(without) != set(_WITHOUT_STATE):
        raise RulesError(f"without_state needs: {', '.join(_WITHOUT_STATE)}")
    if not all(isinstance(entry, dict) for entry in without.values()):
        raise RulesError("invalid without_state")
    return Rules(
        MappingProxyType(thresholds),
        rules,
        MappingProxyType({name: _verdict(without[name], name) for name in _WITHOUT_STATE}),
    )


def _matches(rule: Rule, state: GatewayState, rules: Rules, now: datetime) -> bool:
    when = rule.when
    heartbeat = state.heartbeat
    too_old = when.older_than is None or (
        heartbeat is not None and now - heartbeat.at > rules.threshold(when.older_than)
    )
    by_command = heartbeat is not None and heartbeat.written_by_command
    return (
        (not when.state or state.state in when.state)
        and (not when.exit_reason or state.exit_reason in when.exit_reason)
        and (when.desired_state is None or state.desired_state == when.desired_state)
        and (by_command or not when.written_by_command)
        and (heartbeat is None or not when.no_date)
        and too_old
    )


def evaluate(
    reading: GatewayReading, rules: Rules, *, now: datetime, missing_since: datetime | None
) -> Verdict:
    """The diagnosis of one gateway. `missing_since` is when its state file disappeared."""
    state = reading.state
    if state is None:
        missing = any(
            item.code is AnomalyCode.SOURCE_MISSING and (item.source or "").endswith(STATE_FILE)
            for item in reading.anomalies
        )
        if not missing:
            return rules.without_state["unusable"]
        too_long = now - (missing_since or now) > rules.threshold("missing_critical_after_minutes")
        return rules.without_state["missing_too_long" if too_long else "missing"]
    verdict = next(r.verdict for r in rules.rules if _matches(r, state, rules, now))
    # A repair must never work against the operator, nor rely on an intent STETHO cannot read.
    if state.desired_state == "stopped" or state.desired_state_unrecognised:
        verdict = replace(verdict, actions=())
    # A value STETHO does not know is at least a warning.
    unexpected = any(item.code is AnomalyCode.UNEXPECTED_VALUE for item in reading.anomalies)
    if unexpected and verdict.status in (Status.OK, Status.UNKNOWN):
        summary = "online_unknown_value" if verdict.status is Status.OK else verdict.summary
        verdict = replace(verdict, status=Status.WARN, summary=summary)
    return verdict
