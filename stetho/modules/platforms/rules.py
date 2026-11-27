"""Applies the rules of `rules.yaml` to one platform entry."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType

from stetho.modules.gateway.rules import RulesError
from stetho.modules.platforms.reader import PlatformEntry
from stetho.patterns import ACTION_CODE
from stetho.results import Status
from stetho.yamlfile import load_yaml

_RULES_FILE = Path(__file__).parent / "rules.yaml"


class Kind(StrEnum):
    CONNECTED = "connected"
    INACTIVE = "inactive"
    BROKEN = "broken"
    WAITING = "waiting"


@dataclass(frozen=True)
class Rules:
    critical_after: timedelta
    states: Mapping[str, Kind]
    repair: str


@dataclass(frozen=True)
class Verdict:
    status: Status
    summary: str  # key under platforms.summary
    advice: str | None = None  # key under platforms.advice
    repair: str | None = None


def load_rules(overrides: Mapping[str, int] | None = None, path: Path = _RULES_FILE) -> Rules:
    document = load_yaml(path)
    if not isinstance(document, dict) or document.keys() != {"thresholds", "states", "repair"}:
        raise RulesError("platforms: expected thresholds, states and repair")
    thresholds = document["thresholds"]
    if not isinstance(thresholds, dict) or thresholds.keys() != {"critical_after_minutes"}:
        raise RulesError("platforms: invalid thresholds")
    if unknown := set(overrides or {}) - set(thresholds):
        raise RulesError(f"platforms: unknown threshold: {', '.join(sorted(unknown))}")
    minutes = {**thresholds, **(overrides or {})}["critical_after_minutes"]
    if not isinstance(minutes, int) or minutes < 1:
        raise RulesError("platforms: invalid thresholds")
    states = document["states"]
    if not isinstance(states, dict) or not all(value in set(Kind) for value in states.values()):
        raise RulesError("platforms: invalid states")
    repair = document["repair"]
    if not isinstance(repair, str) or not ACTION_CODE.fullmatch(repair):
        raise RulesError("platforms: invalid repair")
    return Rules(
        timedelta(minutes=minutes),
        MappingProxyType({name: Kind(value) for name, value in states.items()}),
        repair,
    )


def _of_state(entry: PlatformEntry, rules: Rules, now: datetime, since: datetime) -> Verdict:
    kind = None if entry.state is None else rules.states.get(entry.state)
    if kind is Kind.CONNECTED:
        return Verdict(Status.OK, "connected")
    if kind is Kind.INACTIVE:
        return Verdict(Status.INFO, str(entry.state))
    if kind is Kind.BROKEN:
        return Verdict(Status.CRITICAL, "broken", "check_settings")
    if kind is Kind.WAITING:
        if now - since > rules.critical_after:
            return Verdict(Status.CRITICAL, "waiting", "check_connection", rules.repair)
        return Verdict(Status.WARN, "waiting", "wait")
    return Verdict(Status.WARN, "unknown_state", "check_version")


def is_waiting(entry: PlatformEntry, rules: Rules) -> bool:
    return entry.state is not None and rules.states.get(entry.state) is Kind.WAITING


def evaluate(entry: PlatformEntry, rules: Rules, *, now: datetime, since: datetime) -> Verdict:
    """The diagnosis of an entry of the running gateway. `since`: when the waiting began."""
    verdict = _of_state(entry, rules, now, since)
    if not entry.needs_attention:
        return verdict
    if verdict.status in (Status.OK, Status.INFO):
        return Verdict(Status.WARN, "attention", "check_attention")
    if verdict.advice == "wait":
        return replace(verdict, advice="check_attention")
    return verdict
