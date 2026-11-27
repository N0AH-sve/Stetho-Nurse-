"""The gateway sensor: reads every gateway, applies the rules and builds one result."""

from dataclasses import dataclass
from datetime import datetime

from stetho.anomalies import Anomaly, AnomalyCode
from stetho.config import Config
from stetho.durations import duration_text
from stetho.i18n import Translator
from stetho.modules.gateway.reader import (
    MODULE,
    ROOT_PROFILE,
    STATE_FILE,
    GatewayReading,
    load_adapter,
    read_gateway,
    read_gateways,
)
from stetho.modules.gateway.rules import Verdict, evaluate, load_rules
from stetho.results import Detail, Item, SensorOutcome, SensorResult, Status, worst_status
from stetho.sources import Sleep


@dataclass(frozen=True)
class _Diagnosis:
    profile: str
    verdict: Verdict
    summary: str
    details: tuple[Detail, ...]


class GatewaySensor:
    """Remembers, until STETHO restarts, the profiles seen and since when a file is missing."""

    id = MODULE

    def __init__(self, config: Config, translator: Translator) -> None:
        self._hermes = config.hermes
        self._timezone = config.timezone
        self._adapter = load_adapter()
        self._rules = load_rules(config.modules.gateway.overrides)  # may raise `RulesError`
        self._translator = translator
        self._seen: set[str] = set()
        self._missing_since: dict[str, datetime] = {}

    @property
    def title(self) -> str:
        return self._translator.text("gateway.title")

    @property
    def group(self) -> str:
        return self._translator.text("gateway.group")

    def read(self, now: datetime, sleep: Sleep) -> SensorOutcome:
        readings, global_anomalies = self._read_all(now, sleep)
        expected = {reading.profile for reading in readings}
        self._missing_since = {
            name: since for name, since in self._missing_since.items() if name in expected
        }
        diagnoses = tuple(self._diagnose(reading, now) for reading in readings)
        anomalies = (*global_anomalies, *(a for reading in readings for a in reading.anomalies))
        return SensorOutcome(self._result(diagnoses, global_anomalies, anomalies, now), anomalies)

    def _read_all(
        self, now: datetime, sleep: Sleep
    ) -> tuple[list[GatewayReading], tuple[Anomaly, ...]]:
        found = read_gateways(self._hermes, adapter=self._adapter, now=now, sleep=sleep)
        readings = list(found.gateways)
        if self._hermes.profiles == "auto":
            # A profile seen once stays expected: its file must not disappear unnoticed.
            listed = {reading.profile for reading in readings}
            served = {
                name
                for reading in readings
                if reading.profile == ROOT_PROFILE and reading.state
                for name in reading.state.served_profiles
            }
            for profile in sorted(self._seen - listed - served):
                readings.append(
                    read_gateway(
                        self._hermes.data_dir,
                        profile,
                        adapter=self._adapter,
                        now=now,
                        sleep=sleep,
                    )
                )
            self._seen = {reading.profile for reading in readings}
        return readings, found.anomalies

    def _diagnose(self, reading: GatewayReading, now: datetime) -> _Diagnosis:
        missing = any(
            item.code is AnomalyCode.SOURCE_MISSING and (item.source or "").endswith(STATE_FILE)
            for item in reading.anomalies
        )
        since = self._missing_since.setdefault(reading.profile, now) if missing else None
        if not missing:
            self._missing_since.pop(reading.profile, None)
        verdict = evaluate(reading, self._rules, now=now, missing_since=since)
        return _Diagnosis(
            reading.profile,
            verdict,
            self._summary(reading, verdict, since, now),
            self._details(reading, now),
        )

    def _summary(
        self, reading: GatewayReading, verdict: Verdict, since: datetime | None, now: datetime
    ) -> str:
        heartbeat = reading.state.heartbeat if reading.state else None
        start = heartbeat.at if heartbeat else since
        key = f"gateway.summary.{verdict.summary}"
        if "age" in self._translator.placeholders(key):
            age = duration_text(now - (start or now), self._translator)
            return self._translator.text(key, age=age)
        return self._translator.text(key)

    def _details(self, reading: GatewayReading, now: datetime) -> tuple[Detail, ...]:
        text = self._translator.text
        details: list[Detail] = []

        def add(name: str, value: str) -> None:
            details.append(Detail(label=text(f"gateway.detail.{name}"), value=value))

        state = reading.state
        if state is not None:
            if state.heartbeat is not None and not state.heartbeat.written_by_command:
                age = duration_text(now - state.heartbeat.at, self._translator)
                add("last_sign_of_life", text("gateway.value.ago", age=age))
            if state.exit_reason is not None:
                add("stop_reason", text(f"gateway.exit_reason.{state.exit_reason}"))
            elif state.has_other_exit_reason:
                add("stop_reason", text("gateway.value.reason_not_shown"))
            if state.active_agents is not None:
                add("active_agents", str(state.active_agents))
            if state.hermes_version is not None:
                add("hermes_version", state.hermes_version)
        stop = reading.last_unclean_stop
        if stop is not None:
            date = stop.detected_at.astimezone(self._timezone).strftime("%Y-%m-%d %H:%M")
            key = "unclean_stop_oom" if stop.suspected_oom else "unclean_stop"
            add("last_unclean_stop", text(f"gateway.value.{key}", date=date))
        return tuple(details)

    def _advice(self, key: str | None) -> str | None:
        return None if key is None else self._translator.text(f"gateway.advice.{key}")

    def _actions(self, diagnosis: _Diagnosis) -> tuple[str, ...]:
        # An action names no profile: NURSE would act on the main one.
        return diagnosis.verdict.actions if diagnosis.profile == ROOT_PROFILE else ()

    def _label(self, profile: str) -> str:
        return self._hermes.profile_labels.get(profile, profile)

    def _result(
        self,
        diagnoses: tuple[_Diagnosis, ...],
        global_anomalies: tuple[Anomaly, ...],
        anomalies: tuple[Anomaly, ...],
        now: datetime,
    ) -> SensorResult:
        text = self._translator.text
        statuses = [diagnosis.verdict.status for diagnosis in diagnoses]
        if global_anomalies or not diagnoses:
            statuses.append(Status.UNKNOWN)  # something could not be checked
        status = worst_status(statuses) or Status.UNKNOWN
        items: tuple[Item, ...] = ()
        details: tuple[Detail, ...] = ()
        actions: tuple[str, ...] = ()
        advice: str | None = None
        # The sentence of the card comes from the first gateway that has the worst status.
        worst = next((d for d in diagnoses if d.verdict.status is status), None)
        if len(diagnoses) == 1:
            details = diagnoses[0].details
            actions = self._actions(diagnoses[0])
        elif diagnoses:
            items = tuple(
                Item(
                    id=d.profile,
                    label=self._label(d.profile),
                    status=d.verdict.status,
                    summary=d.summary,
                    advice=self._advice(d.verdict.advice),
                    details=d.details,
                    actions=self._actions(d),
                )
                for d in diagnoses
            )
        if worst is not None and status is not Status.OK:
            advice = self._advice(worst.verdict.advice)
        if not diagnoses:
            summary = text("gateway.summary.no_gateway")
            advice = self._advice("check_profile_list")
        elif worst is None:
            summary = text("gateway.summary.profiles_unchecked")
            advice = self._advice("check_profiles_access")
        elif len(diagnoses) == 1:
            summary = worst.summary
        elif status is Status.OK:
            summary = text("gateway.summary.all_online", count=len(diagnoses))
        else:
            summary = text(
                "gateway.summary.of_profile",
                name=self._label(worst.profile),
                summary=worst.summary,
            )
        return SensorResult(
            id=MODULE,
            title=self.title,
            group=self.group,
            status=status,
            summary=summary,
            advice=advice,
            checked_at=now,
            details=details,
            items=items,
            actions=actions,
            anomalies=tuple(dict.fromkeys(item.code for item in anomalies)),
        )
