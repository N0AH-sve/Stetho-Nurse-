import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from stetho.anomalies import AnomalyCode
from stetho.config import PLATFORMS_THRESHOLDS, Config, Language, parse_config
from stetho.i18n import load_translator
from stetho.modules.gateway.rules import RulesError
from stetho.modules.platforms.reader import load_adapter
from stetho.modules.platforms.rules import load_rules
from stetho.modules.platforms.sensor import PlatformsSensor
from stetho.results import Item, SensorOutcome, SensorResult, Status

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "hermes-0.21"
RULES_FILE = ROOT / "stetho" / "modules" / "platforms" / "rules.yaml"
NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
MARKER = "MADE-UP private text"
PID = {"pid": 101, "start_time": 123456}


def config(data_dir: Path, **modules: Any) -> Config:
    document = yaml.safe_load((ROOT / "config" / "stetho.example.yaml").read_text("utf-8"))
    document["hermes"]["data_dir"] = str(data_dir)
    for name, changes in modules.items():
        document["modules"][name] |= changes
    return parse_config(document)


def sensor(data_dir: Path, language: Language = Language.EN, **modules: Any) -> PlatformsSensor:
    return PlatformsSensor(config(data_dir, **modules), load_translator(language))


def read(item: PlatformsSensor, now: datetime = NOW) -> SensorOutcome:
    return item.read(now, lambda _: None)


def platform(state: str = "connected", **changes: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "state": state,
        "error_code": None,
        "error_message": None,
        "needs_attention": False,
        "retrying_since": None,
        "updated_at": "2026-01-15T08:00:05+00:00",
        "writer_pid": 101,
        "writer_start_time": 123456,
    }
    return entry | changes


def write(home: Path, platforms: object, **gateway: Any) -> None:
    document: dict[str, Any] = {
        **PID,
        "gateway_state": "running",
        "updated_at": (NOW - timedelta(seconds=30)).isoformat(),
        "platforms": platforms,
    }
    home.mkdir(parents=True, exist_ok=True)
    (home / "gateway_state.json").write_text(json.dumps(document | gateway), encoding="utf-8")


def card(tmp_path: Path, platforms: object, **gateway: Any) -> SensorResult:
    write(tmp_path, platforms, **gateway)
    return read(sensor(tmp_path)).result


def only(result: SensorResult) -> Item:
    (item,) = result.items
    return item


def since(minutes: float) -> str:
    return (NOW - timedelta(minutes=minutes)).isoformat()


def test_connected_platforms_are_healthy(tmp_path: Path) -> None:
    result = card(tmp_path, {"discord": platform(), "telegram": platform()})

    assert (result.id, result.title, result.group) == ("platforms", "Platforms", "Availability")
    assert (result.status, result.summary) == (Status.OK, "All platforms are connected (2)")
    assert [(item.id, item.label, item.status) for item in result.items] == [
        ("default/discord", "Discord", Status.OK),
        ("default/telegram", "Telegram", Status.OK),
    ]
    assert (result.advice, result.assessed, result.anomalies) == (None, True, ())


@pytest.mark.parametrize("state", ["connecting", "retrying", "disconnected"])
@pytest.mark.parametrize(
    ("minutes", "status", "actions"),
    [
        (1, Status.WARN, ()),
        (5, Status.WARN, ()),
        (5.1, Status.CRITICAL, ("RESTART_GATEWAY",)),
    ],
)
def test_waiting_platform_becomes_critical_after_five_minutes(
    tmp_path: Path, state: str, minutes: float, status: Status, actions: tuple[str, ...]
) -> None:
    item = only(card(tmp_path, {"discord": platform(state, retrying_since=since(minutes))}))

    assert (item.status, item.actions) == (status, actions)
    assert item.advice


def test_without_a_date_the_delay_starts_when_first_seen(tmp_path: Path) -> None:
    gateway = sensor(tmp_path)
    results = []
    for minutes in (0, 5, 6):
        moment = NOW + timedelta(minutes=minutes)
        write(tmp_path, {"discord": platform("retrying")}, updated_at=moment.isoformat())
        results.append(only(read(gateway, moment).result))

    assert [item.status for item in results] == [Status.WARN, Status.WARN, Status.CRITICAL]
    assert results[-1].summary == "Not connected for 6 min"


def test_delay_starts_again_after_a_connection(tmp_path: Path) -> None:
    gateway = sensor(tmp_path)
    for minutes, state in ((0, "retrying"), (1, "connected"), (20, "retrying")):
        moment = NOW + timedelta(minutes=minutes)
        write(tmp_path, {"discord": platform(state)}, updated_at=moment.isoformat())
        result = read(gateway, moment).result

    assert only(result).status is Status.WARN


def test_threshold_can_be_changed(tmp_path: Path) -> None:
    write(tmp_path, {"discord": platform("retrying", retrying_since=since(8))})
    gateway = sensor(tmp_path, platforms={"overrides": {"critical_after_minutes": 10}})

    assert only(read(gateway).result).status is Status.WARN


def test_fatal_platform_is_critical_without_repair(tmp_path: Path) -> None:
    result = card(tmp_path, {"discord": platform("fatal", error_message=MARKER)})

    item = only(result)
    assert (item.status, item.actions) == (Status.CRITICAL, ())
    assert result.summary == "Discord: Not connected: Hermes gave up on this platform"
    assert result.advice == item.advice
    assert [(d.label, d.value) for d in item.details] == [
        ("Error", "Not shown: see the Hermes logs")
    ]


@pytest.mark.parametrize("state", ["disabled", "paused"])
def test_platform_not_used_on_purpose_is_only_shown(tmp_path: Path, state: str) -> None:
    result = card(tmp_path, {"discord": platform(state)})

    assert (only(result).status, only(result).actions) == (Status.INFO, ())
    assert (result.status, result.summary) == (Status.INFO, "No platform to monitor")


def test_nothing_to_monitor_with_an_empty_list(tmp_path: Path) -> None:
    result = card(tmp_path, {})

    assert (result.status, result.summary, result.items) == (
        Status.INFO,
        "No platform to monitor",
        (),
    )


@pytest.mark.parametrize("state", ["connected", "paused", "disabled", "connecting"])
def test_platform_asking_for_attention_is_at_least_a_warning(tmp_path: Path, state: str) -> None:
    item = only(card(tmp_path, {"discord": platform(state, needs_attention=True)}))

    assert item.status is Status.WARN


def test_attention_never_lowers_a_critical_platform(tmp_path: Path) -> None:
    item = only(card(tmp_path, {"discord": platform("fatal", needs_attention=True)}))

    assert item.status is Status.CRITICAL


@pytest.mark.parametrize(
    "writer",
    [{"writer_pid": 90}, {"writer_start_time": 1}, {"writer_pid": None}],
)
def test_entry_of_an_older_process_is_only_shown(tmp_path: Path, writer: dict[str, Any]) -> None:
    former_entry = platform("connected") | writer

    result = card(tmp_path, {"discord": platform(), "gone": former_entry})

    former = result.items[1]
    assert (former.status, former.summary) == (Status.INFO, "Former state, not checked")
    assert (result.status, result.summary) == (Status.OK, "All platforms are connected (1)")


def test_without_any_entry_of_the_running_process_nothing_is_healthy(tmp_path: Path) -> None:
    platforms = {"discord": platform(writer_pid=90), "x": platform("fatal", writer_pid=90)}

    result = card(tmp_path, platforms)

    assert [item.status for item in result.items] == [Status.INFO, Status.INFO]
    assert (result.status, result.assessed) == (Status.UNKNOWN, True)
    assert result.summary == "No platform state comes from the running gateway"


def test_file_without_a_process_number_proves_nothing(tmp_path: Path) -> None:
    result = card(tmp_path, {"discord": platform()}, pid=None)

    assert result.status is Status.UNKNOWN


@pytest.mark.parametrize(
    "gateway",
    [
        {"gateway_state": "stopped"},
        {"gateway_state": "startup_failed"},
        {"gateway_state": "degraded", "exit_reason": "shutdown_watchdog"},
        {"updated_at": (NOW - timedelta(minutes=3)).isoformat()},
        {"updated_at": int(NOW.timestamp())},
        {"updated_at": "not a date"},
    ],
)
@pytest.mark.parametrize("state", ["connected", "fatal"])
def test_platforms_of_a_gateway_that_does_not_run_are_not_assessed(
    tmp_path: Path, gateway: dict[str, Any], state: str
) -> None:
    result = card(tmp_path, {"discord": platform(state)}, **gateway)

    item = only(result)
    assert (item.status, item.assessed, item.actions, item.advice, item.details) == (
        Status.UNKNOWN,
        False,
        (),
        None,
        (),
    )
    assert (result.status, result.assessed, result.anomalies) == (Status.UNKNOWN, False, ())
    assert result.summary == "Not assessed: the gateway is not running normally"


@pytest.mark.parametrize("state", ["starting", "draining", "degraded", "something new"])
def test_gateway_with_a_recent_sign_of_life_is_assessed(tmp_path: Path, state: str) -> None:
    result = card(tmp_path, {"discord": platform()}, gateway_state=state)

    assert (result.status, result.assessed) == (Status.OK, True)


def test_late_limit_follows_the_gateway_threshold(tmp_path: Path) -> None:
    write(tmp_path, {"discord": platform()}, updated_at=since(3))
    gateway = sensor(tmp_path, gateway={"overrides": {"late_after_minutes": 4}})

    assert read(gateway).result.assessed


@pytest.mark.parametrize("content", [None, "{", '{"gateway_state": "running"}'])
def test_unusable_file_is_left_to_the_gateway_module(tmp_path: Path, content: str | None) -> None:
    if content is not None:
        (tmp_path / "gateway_state.json").write_text(content, encoding="utf-8")

    result = read(sensor(tmp_path)).result

    assert (result.status, result.assessed, result.items) == (Status.UNKNOWN, False, ())


def test_unusable_file_is_explained_here_without_the_gateway_module(tmp_path: Path) -> None:
    gateway = sensor(tmp_path, gateway={"enabled": False})

    outcome = read(gateway)

    assert (outcome.result.status, outcome.result.assessed) == (Status.UNKNOWN, True)
    assert outcome.result.summary == "The platforms cannot be checked"
    assert [(a.module, a.code) for a in outcome.anomalies] == [
        ("platforms", AnomalyCode.SOURCE_MISSING)
    ]


@pytest.mark.parametrize(
    "platforms",
    [
        None,
        [],
        "x",
        {"bad name!": platform()},
        {"a:b:c": platform()},
        {"discord": "x"},
        {"discord": {}},
    ],
)
def test_unusable_platforms_are_never_healthy(tmp_path: Path, platforms: object) -> None:
    result = card(tmp_path, platforms)

    assert (result.status, result.assessed) == (Status.UNKNOWN, True)
    assert result.anomalies == (AnomalyCode.SCHEMA_CHANGED,)


def test_one_bad_entry_does_not_hide_the_others(tmp_path: Path) -> None:
    result = card(tmp_path, {"discord": platform("fatal"), "telegram": 3})

    assert result.status is Status.CRITICAL
    assert [item.id for item in result.items] == ["default/discord"]


@pytest.mark.parametrize("value", ["sleeping", MARKER, None, 3])
def test_unknown_state_is_a_warning_and_is_filtered(tmp_path: Path, value: object) -> None:
    write(tmp_path, {"discord": platform() | {"state": value}})

    outcome = read(sensor(tmp_path))

    assert only(outcome.result).status is Status.WARN
    assert outcome.result.anomalies == (AnomalyCode.UNEXPECTED_VALUE,)
    assert MARKER not in repr(outcome)


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"needs_attention": "yes"}, AnomalyCode.SCHEMA_CHANGED),
        ({"retrying_since": "soon"}, AnomalyCode.SCHEMA_CHANGED),
        ({"retrying_since": "2026-01-15T11:00:00"}, AnomalyCode.SCHEMA_CHANGED),
        ({"retrying_since": 12}, AnomalyCode.SCHEMA_CHANGED),
        ({"retrying_since": "2027-01-01T00:00:00+00:00"}, AnomalyCode.IMPLAUSIBLE_VALUE),
        ({"retrying_since": "2001-01-01T00:00:00+00:00"}, AnomalyCode.IMPLAUSIBLE_VALUE),
        ({"new_field": 1}, AnomalyCode.SCHEMA_CHANGED),
        ({MARKER: 1}, AnomalyCode.SCHEMA_CHANGED),
    ],
)
def test_unusual_value_is_reported_and_not_used(
    tmp_path: Path, changes: dict[str, Any], code: AnomalyCode
) -> None:
    write(tmp_path, {"discord": platform("retrying") | changes})

    outcome = read(sensor(tmp_path))

    assert only(outcome.result).status is Status.WARN
    assert outcome.result.anomalies == (code,)
    assert MARKER not in repr(outcome)


def test_free_text_never_appears(tmp_path: Path) -> None:
    entry = platform("retrying", error_message=MARKER, error_code=MARKER)
    write(tmp_path, {"discord": entry}, exit_reason=MARKER)

    assert MARKER not in repr(read(sensor(tmp_path)))


def test_healthy_platform_hides_an_old_error_detail(tmp_path: Path) -> None:
    assert only(card(tmp_path, {"discord": platform(error_message=MARKER)})).details == ()


@pytest.mark.parametrize("intent", ["stopped", "paused for now"])
def test_no_repair_against_or_without_a_known_intent(tmp_path: Path, intent: str) -> None:
    entry = platform("retrying", retrying_since=since(10))

    item = only(card(tmp_path, {"discord": entry}, desired_state=intent))

    assert (item.status, item.actions) == (Status.CRITICAL, ())


def test_secondary_profile_gets_no_action_and_is_named(tmp_path: Path) -> None:
    write(tmp_path, {"discord": platform()})
    waiting = platform("retrying", retrying_since=since(10))
    write(tmp_path / "profiles" / "work", {"discord": waiting})

    result = read(sensor(tmp_path)).result

    assert [(item.id, item.label, item.actions) for item in result.items] == [
        ("default/discord", "Main · Discord", ()),
        ("work/discord", "work · Discord", ()),
    ]
    assert result.summary == "work · Discord: Not connected for 10 min"


def test_platform_of_a_served_profile_is_named_after_it(tmp_path: Path) -> None:
    entry = platform("retrying", retrying_since=since(10))
    write(tmp_path, {"work:telegram": entry}, served_profiles=["work"])

    item = only(read(sensor(tmp_path)).result)

    assert (item.id, item.label) == ("default/work:telegram", "work · Telegram")
    # Restarting the main gateway restarts the platforms it serves.
    assert item.actions == ("RESTART_GATEWAY",)


@pytest.mark.parametrize(
    ("main", "status", "assessed"),
    [("connected", Status.UNKNOWN, False), ("fatal", Status.CRITICAL, True)],
)
def test_one_gateway_down_prevents_a_healthy_card(
    tmp_path: Path, main: str, status: Status, assessed: bool
) -> None:
    write(tmp_path, {"discord": platform(main)})
    write(tmp_path / "profiles" / "work", {"discord": platform()}, gateway_state="stopped")

    result = read(sensor(tmp_path)).result

    assert (result.status, result.assessed) == (status, assessed)
    assert result.items[1].assessed is False


def test_card_stays_assessed_when_it_holds_an_anomaly(tmp_path: Path) -> None:
    write(tmp_path, {"discord": platform(new_field=1)})
    write(tmp_path / "profiles" / "work", {"discord": platform()}, gateway_state="stopped")

    result = read(sensor(tmp_path)).result

    assert (result.status, result.assessed) == (Status.UNKNOWN, True)


def test_profiles_that_cannot_be_listed_prevent_a_healthy_card(tmp_path: Path) -> None:
    write(tmp_path, {"discord": platform()})
    write(tmp_path / "profiles" / "not a name", {})

    result = read(sensor(tmp_path)).result

    assert (result.status, result.assessed) == (Status.UNKNOWN, True)
    assert result.summary == "The platforms cannot be checked"


def test_profile_that_disappears_stays_expected(tmp_path: Path) -> None:
    write(tmp_path, {"discord": platform()})
    write(tmp_path / "profiles" / "work", {"discord": platform()})
    gateway = sensor(tmp_path)
    read(gateway)
    (tmp_path / "profiles" / "work" / "gateway_state.json").unlink()

    assert read(gateway).result.status is Status.UNKNOWN


@pytest.mark.parametrize(
    ("language", "title"),
    [(Language.EN, "Platforms"), (Language.FR, "Plateformes"), (Language.ES, "Plataformas")],
)
def test_degraded_scenario_in_each_language(language: Language, title: str) -> None:
    result = read(sensor(FIXTURES / "degraded", language)).result

    assert (result.title, result.status) == (title, Status.CRITICAL)
    assert [item.status for item in result.items] == [Status.CRITICAL, Status.CRITICAL]


@pytest.mark.parametrize("language", list(Language))
def test_every_text_of_the_module_exists(language: Language) -> None:
    translator = load_translator(language)
    summaries = (
        "connected", "disabled", "paused", "broken", "unknown_state", "attention", "old",
        "not_assessed", "unusable", "none_current", "nothing",
    )  # fmt: skip
    advices = (
        "check_settings", "check_connection", "wait", "check_version", "check_attention",
        "check_data",
    )  # fmt: skip

    for name in summaries:
        assert translator.text(f"platforms.summary.{name}")
    for name in advices:
        assert translator.text(f"platforms.advice.{name}")


def test_every_state_of_the_adapter_has_a_rule() -> None:
    assert set(load_rules().states) == set(load_adapter().platform_states)
    assert set(PLATFORMS_THRESHOLDS) == {"critical_after_minutes"}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"extra": 1}, "expected thresholds, states and repair"),
        ({"thresholds": {}}, "invalid thresholds"),
        ({"thresholds": {"critical_after_minutes": 0}}, "invalid thresholds"),
        ({"states": {"x": "other"}}, "invalid states"),
        ({"repair": "restart"}, "invalid repair"),
    ],
)
def test_invalid_rules_are_refused(tmp_path: Path, change: dict[str, Any], message: str) -> None:
    document = yaml.safe_load(RULES_FILE.read_text(encoding="utf-8")) | change
    (tmp_path / "rules.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(RulesError, match=message):
        load_rules(path=tmp_path / "rules.yaml")


def test_unknown_override_is_refused() -> None:
    with pytest.raises(RulesError, match="unknown threshold"):
        load_rules({"other": 1})


@pytest.mark.parametrize("content", ["[]\n", "known_fields: [1]\nplatform_states: []\n"])
def test_invalid_adapter_is_refused(tmp_path: Path, content: str) -> None:
    (tmp_path / "adapter.yaml").write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match="invalid platforms adapter"):
        load_adapter(tmp_path / "adapter.yaml")
