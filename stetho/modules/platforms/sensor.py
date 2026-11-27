"""The platforms sensor: one item for each platform of each gateway."""

from dataclasses import dataclass
from datetime import datetime

from stetho.anomalies import Anomaly
from stetho.config import Config
from stetho.durations import duration_text
from stetho.i18n import Translator
from stetho.modules.gateway.reader import (
    ROOT_PROFILE,
    GatewayState,
    ProfileReader,
)
from stetho.modules.gateway.reader import load_adapter as load_gateway_adapter
from stetho.modules.gateway.rules import load_rules as load_gateway_rules
from stetho.modules.platforms.reader import (
    MODULE,
    Adapters,
    PlatformEntry,
    PlatformsReading,
    load_adapter,
    read_platforms,
)
from stetho.modules.platforms.rules import evaluate, is_waiting, load_rules
from stetho.results import Detail, Item, SensorOutcome, SensorResult, Status, worst_status
from stetho.sources import Sleep

_DOWN_STATES = frozenset({"stopped", "startup_failed"})


@dataclass(frozen=True)
class _Card:
    """What one gateway adds to the card."""

    items: tuple[Item, ...]
    assessed: bool  # False: its gateway is not running normally
    # A reason, other than its items, why something could not be checked.
    unknown_reason: str | None = None  # key under platforms.summary
    advice: str | None = None  # key under platforms.advice


def _served_by(reading: PlatformsReading) -> tuple[str, ...]:
    return reading.gateway.served_profiles if reading.gateway else ()


class PlatformsSensor:
    """Remembers, until STETHO restarts, since when a platform waits and the profiles seen."""

    id = MODULE

    def __init__(self, config: Config, translator: Translator) -> None:
        self._config = config
        self._translator = translator
        self._adapters = Adapters(load_adapter(), load_gateway_adapter())
        self._rules = load_rules(config.modules.platforms.overrides)
        gateway_rules = load_gateway_rules(config.modules.gateway.overrides)
        self._late_after = gateway_rules.threshold("late_after_minutes")
        self._profiles = ProfileReader(config.hermes, MODULE, _served_by)
        # By profile and platform: first time seen waiting, and the gateway process then.
        self._waiting_since: dict[tuple[str, str], tuple[datetime, tuple[object, object]]] = {}

    @property
    def title(self) -> str:
        return self._translator.text("platforms.title")

    @property
    def group(self) -> str:
        return self._translator.text("platforms.group")

    def read(self, now: datetime, sleep: Sleep) -> SensorOutcome:
        found, global_anomalies = self._profiles.read(
            lambda profile: read_platforms(
                self._config.hermes.data_dir,
                profile,
                adapters=self._adapters,
                now=now,
                sleep=sleep,
            )
        )
        readings = [reading for _, reading in found]
        several = len(readings) > 1
        cards: list[_Card] = []
        anomalies: list[Anomaly] = list(global_anomalies)
        for reading in readings:
            if self._alive(reading.gateway, now):
                cards.append(self._card(reading, now, several))
                if reading.complete:
                    self._forget_removed(reading)
                anomalies.extend(reading.anomalies)
            else:
                self._forget_unidentified(reading.profile)
                if not self._config.modules.gateway.enabled:
                    # No gateway card says that something is wrong: this one must.
                    cards.append(self._unexplained(reading, several))
                    anomalies.extend((*reading.file_anomalies, *reading.anomalies))
                else:
                    cards.append(self._not_assessed(reading, several))
        return SensorOutcome(
            self._result(cards, global_anomalies, tuple(anomalies), now), tuple(anomalies)
        )

    def _alive(self, gateway: GatewayState | None, now: datetime) -> bool:
        """Whether the gateway runs now: otherwise its file may say anything."""
        if gateway is None or gateway.state in _DOWN_STATES:
            return False
        if gateway.state == "degraded" and gateway.exit_reason is not None:
            return False  # a watchdog stopped it
        heartbeat = gateway.heartbeat
        return (
            heartbeat is not None
            and not heartbeat.written_by_command
            and now - heartbeat.at <= self._late_after
        )

    def _label(self, reading: PlatformsReading, entry: PlatformEntry, several: bool) -> str:
        name = entry.name[:1].upper() + entry.name[1:]
        profile = entry.served_profile or (reading.profile if several else None)
        if profile is None:
            return name
        label = self._config.hermes.profile_labels.get(profile, profile)
        return f"{label} · {name}"

    def _item_id(self, reading: PlatformsReading, entry: PlatformEntry) -> str:
        return f"{reading.profile}/{entry.key}"

    def _not_assessed(self, reading: PlatformsReading, several: bool) -> _Card:
        text = self._translator.text("platforms.summary.not_assessed")
        items = tuple(
            Item(
                id=self._item_id(reading, entry),
                label=self._label(reading, entry, several),
                status=Status.UNKNOWN,
                summary=text,
                assessed=False,
            )
            for entry in reading.entries
        )
        return _Card(items, False)

    def _forget_removed(self, reading: PlatformsReading) -> None:
        """A platform that a complete reading no longer holds waits no more."""
        present = {entry.key for entry in reading.entries}
        for profile, key in list(self._waiting_since):
            if profile == reading.profile and key not in present:
                del self._waiting_since[profile, key]

    def _forget_unidentified(self, profile: str) -> None:
        """Without both numbers of the process, a restart could go unseen while nothing is read."""
        for key, (_, process) in list(self._waiting_since.items()):
            if key[0] == profile and None in process:
                del self._waiting_since[key]

    def _unexplained(self, reading: PlatformsReading, several: bool) -> _Card:
        usable = reading.gateway is not None and reading.gateway.heartbeat is not None
        items = self._not_assessed(reading, several).items
        if usable:
            assert reading.gateway is not None  # noqa: S101 - for the type checker
            stopped = reading.gateway.desired_state == "stopped"
            return _Card(
                items, True, "gateway_down", "deliberate_stop" if stopped else "check_gateway"
            )
        return _Card(items, True, "unusable", "check_data")

    def _card(self, reading: PlatformsReading, now: datetime, several: bool) -> _Card:
        items = tuple(self._item(reading, entry, now, several) for entry in reading.entries)
        if not reading.complete:
            return _Card(items, True, "unusable", "check_data")
        if reading.entries and not any(entry.current for entry in reading.entries):
            return _Card(items, True, "none_current", "wait")
        return _Card(items, True)

    def _item(
        self,
        reading: PlatformsReading,
        entry: PlatformEntry,
        now: datetime,
        several: bool,
    ) -> Item:
        text = self._translator.text
        identifier = self._item_id(reading, entry)
        label = self._label(reading, entry, several)
        key = (reading.profile, entry.key)
        if not entry.current or not is_waiting(entry, self._rules):
            self._waiting_since.pop(key, None)  # seen otherwise: a new wait starts anew
        if not entry.current:
            former = text("platforms.summary.old")
            return Item(id=identifier, label=label, status=Status.INFO, summary=former)
        since = now
        if is_waiting(entry, self._rules):
            since = self._start_of_wait(reading, entry, now)
        verdict = evaluate(entry, self._rules, now=now, since=since)
        summary_key = f"platforms.summary.{verdict.summary}"
        if "age" in self._translator.placeholders(summary_key):
            summary = text(summary_key, age=duration_text(now - since, self._translator))
        else:
            summary = text(summary_key)
        details: tuple[Detail, ...] = ()
        if entry.has_error_text and verdict.status in (Status.WARN, Status.CRITICAL):
            details = (
                Detail(
                    label=text("platforms.detail.error"), value=text("platforms.value.not_shown")
                ),
            )
        return Item(
            id=identifier,
            label=label,
            status=verdict.status,
            summary=summary,
            advice=None if verdict.advice is None else text(f"platforms.advice.{verdict.advice}"),
            details=details,
            actions=self._actions(reading, verdict.repair),
        )

    def _start_of_wait(
        self, reading: PlatformsReading, entry: PlatformEntry, now: datetime
    ) -> datetime:
        """Kept while the same gateway process runs, even across readings that skip it."""
        key = (reading.profile, entry.key)
        known = self._waiting_since.get(key)
        first_seen = now if known is None or known[1] != reading.process else known[0]
        self._waiting_since[key] = (first_seen, reading.process)
        # The earliest: a date that Hermes moved forward would never become critical.
        return min(first_seen, entry.retrying_since or first_seen)

    def _actions(self, reading: PlatformsReading, repair: str | None) -> tuple[str, ...]:
        gateway = reading.gateway
        # An action names no profile, and must not work against the operator.
        if (
            repair is None
            or reading.profile != ROOT_PROFILE
            or gateway is None
            or gateway.desired_state == "stopped"
            or gateway.desired_state_unrecognised
        ):
            return ()
        return (repair,)

    def _result(
        self,
        cards: list[_Card],
        global_anomalies: tuple[Anomaly, ...],
        anomalies: tuple[Anomaly, ...],
        now: datetime,
    ) -> SensorResult:
        text = self._translator.text
        items = tuple(item for card in cards for item in card.items)
        reason = next((card for card in cards if card.unknown_reason), None)
        down = any(not card.assessed for card in cards)
        unchecked = bool(global_anomalies) or reason is not None or not cards
        statuses = [item.status for item in items]
        if unchecked or down:
            statuses.append(Status.UNKNOWN)
        status = worst_status(statuses) or Status.INFO
        # Unknown only because gateways are down: not a problem of its own.
        assessed = not (
            status is Status.UNKNOWN
            and not unchecked
            and not any(item.assessed and item.status is Status.UNKNOWN for item in items)
            and not anomalies
        )
        advice: str | None = None
        worst = next((item for item in items if item.status is status and item.assessed), None)
        if status is Status.INFO:
            summary = text("platforms.summary.nothing")
        elif status is Status.OK:
            count = sum(item.status is Status.OK for item in items)
            summary = text("platforms.summary.all_connected", count=count)
        elif worst is not None:
            summary = text("platforms.summary.of_platform", name=worst.label, summary=worst.summary)
            advice = worst.advice
        elif reason is not None:
            summary = text(f"platforms.summary.{reason.unknown_reason}")
            # The same words as the gateway card for a stop that was meant.
            module = "gateway" if reason.advice == "deliberate_stop" else "platforms"
            advice = text(f"{module}.advice.{reason.advice}")
        elif down and not unchecked and anomalies:
            summary = text("platforms.summary.partly_assessed")
            advice = text("platforms.advice.check_data")
        elif down and not unchecked:
            summary = text("platforms.summary.not_assessed")
        else:
            summary = text("platforms.summary.unusable")
            advice = text("platforms.advice.check_data")
        return SensorResult(
            id=MODULE,
            title=self.title,
            group=self.group,
            status=status,
            summary=summary,
            advice=advice if assessed else None,
            checked_at=now,
            items=items,
            anomalies=tuple(dict.fromkeys(item.code for item in anomalies)),
            assessed=assessed,
        )
