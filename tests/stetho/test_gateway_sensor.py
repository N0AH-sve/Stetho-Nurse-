import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from stetho.anomalies import AnomalyCode
from stetho.config import Config, Language, parse_config
from stetho.i18n import load_translator
from stetho.modules.gateway.sensor import GatewaySensor
from stetho.results import Detail, SensorOutcome, SensorResult, Status

FIXTURES = Path(__file__).parents[1] / "fixtures" / "hermes-0.21"
EXAMPLE = Path(__file__).parents[2] / "config" / "stetho.example.yaml"
NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


def config(data_dir: Path, profiles: object = "auto", **overrides: int) -> Config:
    document = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    document["hermes"] |= {"data_dir": str(data_dir), "profiles": profiles}
    document["modules"]["gateway"]["overrides"] = overrides
    return parse_config(document)


def sensor(
    data_dir: Path, language: Language = Language.EN, profiles: object = "auto"
) -> GatewaySensor:
    return GatewaySensor(config(data_dir, profiles), load_translator(language))


def no_sleep(_: float) -> None:
    pass


def read(gateway: GatewaySensor, now: datetime = NOW) -> SensorOutcome:
    return gateway.read(now, no_sleep)


def write_state(home: Path, **changes: Any) -> None:
    content = (FIXTURES / "healthy" / "gateway_state.json").read_text(encoding="utf-8")
    home.mkdir(parents=True, exist_ok=True)
    (home / "gateway_state.json").write_text(
        json.dumps(json.loads(content) | changes), encoding="utf-8"
    )


def result_of(data_dir: Path, language: Language = Language.EN) -> SensorResult:
    return read(sensor(data_dir, language)).result


def labelled(details: tuple[Detail, ...]) -> dict[str, str]:
    return {detail.label: detail.value for detail in details}


@pytest.mark.parametrize(
    ("language", "title", "summary"),
    [
        (Language.EN, "Hermes (gateway)", "Online · last sign of life 30 s ago"),
        (Language.FR, "Hermes (gateway)", "En ligne · dernier signe de vie il y a 30 s"),
        (Language.ES, "Hermes (gateway)", "En línea · última señal de vida hace 30 s"),
    ],
)
def test_healthy_gateway_in_each_language(language: Language, title: str, summary: str) -> None:
    result = result_of(FIXTURES / "healthy", language)

    assert (result.id, result.status, result.title) == ("gateway", Status.OK, title)
    assert result.summary == summary
    assert (result.items, result.actions, result.anomalies) == ((), (), ())
    assert result.checked_at == NOW


def test_details_of_a_healthy_gateway() -> None:
    assert labelled(result_of(FIXTURES / "healthy").details) == {
        "Last sign of life": "30 s ago",
        "Active agents": "0",
        "Hermes version": "0.21.4",
    }


@pytest.mark.parametrize(
    ("scenario", "status", "summary", "actions"),
    [
        ("stopped", Status.CRITICAL, "Hermes is stopped", ("RESTART_GATEWAY",)),
        ("startup-failed", Status.CRITICAL, "Hermes could not start", ("RESTART_GATEWAY",)),
        ("degraded", Status.WARN, "Hermes works in degraded mode", ("RESTART_GATEWAY",)),
        ("invalid/truncated-json", Status.UNKNOWN, "The state of Hermes cannot be read", ()),
        ("invalid/missing-field", Status.UNKNOWN, "The state of Hermes cannot be read", ()),
        (
            "invalid/future-date",
            Status.UNKNOWN,
            "The date of the last sign of life cannot be used",
            (),
        ),
        (
            "invalid/unknown-value",
            Status.WARN,
            "Hermes is in a state STETHO does not know",
            (),
        ),
        (
            "invalid/restart-written-by-cli",
            Status.UNKNOWN,
            "A command was sent to Hermes 5 s ago; no sign of life since",
            (),
        ),
    ],
)
def test_scenarios_from_the_files_to_the_result(
    scenario: str, status: Status, summary: str, actions: tuple[str, ...]
) -> None:
    result = result_of(FIXTURES / scenario)

    assert (result.status, result.summary, result.actions) == (status, summary, actions)


def test_unclean_stop_is_shown_in_the_installation_time_zone() -> None:
    details = labelled(result_of(FIXTURES / "degraded", Language.FR).details)

    assert details["Dernier arrêt brutal"] == (
        "Constaté le 2026-01-15 12:30, manque de mémoire possible"
    )


def test_absent_values_leave_their_detail_out(tmp_path: Path) -> None:
    document = {"gateway_state": "running", "updated_at": "2026-01-15T11:59:30+00:00"}
    (tmp_path / "gateway_state.json").write_text(json.dumps(document), encoding="utf-8")

    assert list(labelled(result_of(tmp_path).details)) == ["Last sign of life"]


def test_free_text_reason_is_not_shown() -> None:
    result = result_of(FIXTURES / "startup-failed")

    assert labelled(result.details)["Stop reason"] == "Not shown: see the Hermes logs"
    assert "Made-up startup error" not in repr(result)


def test_known_reason_is_translated(tmp_path: Path) -> None:
    write_state(tmp_path, gateway_state="degraded", exit_reason="shutdown_watchdog")

    details = labelled(result_of(tmp_path).details)

    assert details["Stop reason"] == "Hermes was stuck while stopping"


@pytest.mark.parametrize(
    ("age", "shown"),
    [(timedelta(minutes=7), "7 min"), (timedelta(hours=3), "3 h"), (timedelta(days=2), "2 d")],
)
def test_age_uses_the_largest_unit(tmp_path: Path, age: timedelta, shown: str) -> None:
    write_state(tmp_path, updated_at=(NOW - age).isoformat())

    assert result_of(tmp_path).summary == f"Hermes has not answered for {shown}"


def test_missing_file_becomes_critical_after_five_minutes(tmp_path: Path) -> None:
    gateway = sensor(tmp_path)
    times = [NOW, NOW + timedelta(minutes=5), NOW + timedelta(minutes=6)]

    results = [read(gateway, moment).result for moment in times]

    assert [r.status for r in results] == [Status.UNKNOWN, Status.UNKNOWN, Status.CRITICAL]
    assert results[-1].summary == "No sign of life from Hermes for 6 min"
    assert results[-1].actions == ("RESTART_CONTAINER",)


def test_delay_starts_again_when_the_file_came_back(tmp_path: Path) -> None:
    gateway = sensor(tmp_path)
    read(gateway, NOW)
    write_state(tmp_path)
    read(gateway, NOW + timedelta(minutes=1))
    (tmp_path / "gateway_state.json").unlink()

    result = read(gateway, NOW + timedelta(minutes=10)).result

    assert result.status is Status.UNKNOWN


def two_gateways(tmp_path: Path, **work: Any) -> None:
    write_state(tmp_path)
    write_state(tmp_path / "profiles" / "work", **work)


def test_several_gateways_make_one_item_each(tmp_path: Path) -> None:
    two_gateways(tmp_path)

    result = result_of(tmp_path)

    assert (result.status, result.summary) == (Status.OK, "All gateways are online (2)")
    assert [(item.id, item.label) for item in result.items] == [
        ("default", "Main"),
        ("work", "work"),
    ]
    assert (result.details, result.actions) == ((), ())


def test_card_shows_the_worst_gateway(tmp_path: Path) -> None:
    two_gateways(tmp_path, gateway_state="stopped")

    result = result_of(tmp_path)

    assert (result.status, result.summary) == (Status.CRITICAL, "work: Hermes is stopped")


def test_only_the_main_profile_gets_actions(tmp_path: Path) -> None:
    two_gateways(tmp_path, gateway_state="stopped")
    write_state(tmp_path, gateway_state="stopped")

    result = result_of(tmp_path)

    assert [item.actions for item in result.items] == [("RESTART_GATEWAY",), ()]
    assert result.actions == ()


def test_single_secondary_gateway_gets_no_action(tmp_path: Path) -> None:
    two_gateways(tmp_path, gateway_state="stopped")

    result = read(sensor(tmp_path, profiles=["work"])).result

    assert (result.status, result.actions) == (Status.CRITICAL, ())


def test_profile_that_disappears_stays_expected(tmp_path: Path) -> None:
    two_gateways(tmp_path)
    gateway = sensor(tmp_path)
    read(gateway, NOW)
    shutil.rmtree(tmp_path / "profiles" / "work")

    first = read(gateway, NOW + timedelta(seconds=30)).result
    write_state(tmp_path, updated_at=(NOW + timedelta(minutes=6)).isoformat())
    later = read(gateway, NOW + timedelta(minutes=6)).result

    assert (first.status, later.status) == (Status.UNKNOWN, Status.CRITICAL)
    assert [item.id for item in later.items] == ["default", "work"]


def test_profile_taken_over_by_the_main_gateway_is_dropped(tmp_path: Path) -> None:
    two_gateways(tmp_path)
    gateway = sensor(tmp_path)
    read(gateway, NOW)
    shutil.rmtree(tmp_path / "profiles" / "work")
    write_state(tmp_path, served_profiles=["work"])

    result = read(gateway, NOW).result

    assert (result.status, result.items) == (Status.OK, ())


def test_unchecked_profiles_prevent_a_healthy_card(tmp_path: Path) -> None:
    write_state(tmp_path)
    write_state(tmp_path / "profiles" / "not a name")

    result = result_of(tmp_path)

    assert (result.status, result.summary) == (
        Status.UNKNOWN,
        "Some profiles could not be checked",
    )
    assert result.anomalies == (AnomalyCode.SCHEMA_CHANGED,)


def test_problem_of_a_gateway_wins_over_unchecked_profiles(tmp_path: Path) -> None:
    write_state(tmp_path, gateway_state="stopped")
    write_state(tmp_path / "profiles" / "not a name")

    assert result_of(tmp_path).summary == "Hermes is stopped"


def test_nothing_to_check_is_unknown(tmp_path: Path) -> None:
    write_state(tmp_path, served_profiles=["work"])

    result = read(sensor(tmp_path, profiles=["work"])).result

    assert (result.status, result.summary) == (Status.UNKNOWN, "There is no gateway to check")


def test_all_anomalies_are_returned_and_their_codes_listed_once(tmp_path: Path) -> None:
    two_gateways(tmp_path, new_a=1, new_b=2)

    outcome = read(sensor(tmp_path))

    assert [anomaly.field for anomaly in outcome.anomalies] == ["new_a", "new_b"]
    assert outcome.result.anomalies == (AnomalyCode.SCHEMA_CHANGED,)
    assert outcome.result.status is Status.OK
