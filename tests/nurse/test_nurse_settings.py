import ast
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

from nurse import settings as nurse_settings
from nurse.settings import Action, SettingsError, load_catalogue, load_settings
from stetho.patterns import ACTION_CODE

ROOT = Path(__file__).parents[2]
SETTINGS = ROOT / "config" / "nurse.example.toml"
CATALOGUE = ROOT / "config" / "actions.example.toml"
MARKER = "made-up-private-text"


def toml(value: object) -> str:
    """Enough TOML writing for these tests."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{toml(k)} = {toml(v)}" for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(toml(item) for item in value) + "]"
    if isinstance(value, str):
        escaped = "".join(
            f"\\u{ord(char):04x}" if char in '"\\' or ord(char) < 32 else char for char in value
        )
        return f'"{escaped}"'
    return repr(value)


def changed(source: Path, path: str, value: object) -> dict[str, Any]:
    document = tomllib.loads(source.read_text(encoding="utf-8"))
    *parents, key = path.split(".")
    target: Any = document
    for parent in parents:
        target = target.setdefault(parent, {})
    if value is None:
        del target[key]
    else:
        target[key] = value
    return document


def write(tmp_path: Path, document: dict[str, Any]) -> Path:
    path = tmp_path / "file.toml"
    text = "".join(f"{toml(key)} = {toml(value)}\n" for key, value in document.items())
    path.write_text(text, encoding="utf-8")
    return path


def settings_error(tmp_path: Path, path: str, value: object) -> str:
    with pytest.raises(SettingsError) as caught:
        load_settings(write(tmp_path, changed(SETTINGS, path, value)))
    return str(caught.value)


def catalogue_error(tmp_path: Path, path: str, value: object) -> str:
    with pytest.raises(SettingsError) as caught:
        load_catalogue(write(tmp_path, changed(CATALOGUE, path, value)))
    return str(caught.value)


def test_example_settings_are_valid_and_run_nothing_by_themselves() -> None:
    settings = load_settings(SETTINGS)

    assert (settings.auto_actions, settings.max_actions_per_hour) == (False, 7)
    assert (settings.container, settings.proxy_url) == ("hermes", "http://docker-proxy:2375")
    assert settings.log_dir == Path("/log")


def test_example_catalogue_holds_the_five_actions() -> None:
    actions = load_catalogue(CATALOGUE)

    assert list(actions) == [
        "RESTART_GATEWAY",
        "RESTART_CONTAINER",
        "RERUN_JOB",
        "PAUSE_JOB",
        "UNBLOCK_KANBAN_TASK",
    ]
    assert [code for code, action in actions.items() if action.auto] == ["RESTART_GATEWAY"]
    assert actions["RESTART_CONTAINER"].argv is None
    assert actions["RERUN_JOB"].timeout_seconds == 1800
    assert all((a.auto_per_hour, a.manual_per_hour) == (2, 5) for a in actions.values())
    assert [actions[c].requires for c in actions] == [
        "gateway_may_run",
        "gateway_may_run",
        "job_not_running",
        "job_enabled",
        "task_running",
    ]
    for action in actions.values():
        assert set(action.labels) == {"en", "fr", "es"}
        if action.argv:
            assert action.argv[0] == "hermes"
            assert action.user == "hermes"


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("auto_actions", "yes"),
        ("max_actions_per_hour", 0),
        ("max_actions_per_hour", 101),
        ("max_actions_per_hour", True),
        ("hermes.container", "../other"),
        ("hermes.container", "a/b"),
        ("hermes.container", ""),
        ("hermes.container", "x\n"),
        ("hermes.data_dir", "data"),
        ("hermes.data_dir", "/a/../b"),
        ("docker.proxy_url", "https://docker-proxy:2375"),
        ("docker.proxy_url", "unix:///var/run/docker.sock"),
        ("docker.proxy_url", "http://docker-proxy"),
        ("docker.proxy_url", "http://user@docker-proxy:2375"),
        ("docker.proxy_url", "http://docker-proxy:2375/path"),
        ("docker.proxy_url", "http://docker-proxy:2375?x=1"),
        ("docker.proxy_url", "http://docker-proxy:999999"),
        ("docker.proxy_url", "http://[bad:2375"),
        ("paths.log_dir", "log"),
        ("paths.results_dir", "/shared/prescriptions"),
        ("paths.log_dir", "/shared/results/private"),
        ("paths.log_dir", "/shared"),
        ("paths.prescriptions_dir", "/hermes/prescriptions"),
        ("paths.actions_file", "/shared/results/actions.toml"),
        ("paths.actions_file", "/shared/prescriptions/actions.toml"),
        ("paths.actions_file", "/log/actions.toml"),
    ],
)
def test_dangerous_or_unclear_setting_stops_nurse(tmp_path: Path, path: str, value: object) -> None:
    assert settings_error(tmp_path, path, value)


@pytest.mark.parametrize(
    "path", ["auto_actions", "hermes.container", "docker.proxy_url", "paths.log_dir"]
)
def test_missing_setting_is_refused(tmp_path: Path, path: str) -> None:
    assert settings_error(tmp_path, path, None).endswith("missing")


@pytest.mark.parametrize("path", ["extra", "hermes.socket", "paths.other"])
def test_unknown_setting_is_refused(tmp_path: Path, path: str) -> None:
    assert settings_error(tmp_path, path, 1) == f"unknown setting: {path}"


@pytest.mark.parametrize(
    "argv",
    [
        ["sh", "-c", "hermes gateway restart"],
        ["bash"],
        ["/usr/local/bin/hermes", "gateway", "restart"],
        ["env", "hermes", "gateway", "restart"],
        ["busybox", "sh"],
        ["python3", "-c", "x"],
        ["Hermes", "gateway", "restart"],
        ["hermes "],
        [],
        ["hermes", ""],
        ["hermes", 1],
        ["hermes", "a\0b"],
        ["hermes"] * 33,
        "hermes gateway restart",
    ],
)
def test_only_the_hermes_command_may_be_run(tmp_path: Path, argv: object) -> None:
    assert catalogue_error(tmp_path, "RESTART_GATEWAY.argv", argv).startswith(
        "RESTART_GATEWAY.argv"
    )


@pytest.mark.parametrize(
    "argv",
    [
        ["hermes", "cron", "run"],
        ["hermes", "cron", "run", "{target}", "{target}"],
        ["hermes", "cron", "run", "--id={target}"],
        ["hermes", "cron", "run", "{target}", "{other}"],
        ["hermes", "cron", "run", "{target}x"],
    ],
)
def test_target_is_exactly_one_whole_argument(tmp_path: Path, argv: list[str]) -> None:
    assert "RERUN_JOB.argv" in catalogue_error(tmp_path, "RERUN_JOB.argv", argv)


def test_command_without_target_cannot_use_one(tmp_path: Path) -> None:
    argv = ["hermes", "gateway", "restart", "{target}"]

    assert "only with a target" in catalogue_error(tmp_path, "RESTART_GATEWAY.argv", argv)


@pytest.mark.parametrize(
    "user", ["root", "0", "00", "hermes:root", "0:0", "root ", "-1", "Hermes", "a b", ""]
)
def test_command_never_runs_as_root(tmp_path: Path, user: str) -> None:
    assert "RESTART_GATEWAY.user" in catalogue_error(tmp_path, "RESTART_GATEWAY.user", user)


@pytest.mark.parametrize("user", ["hermes", "10000", "_svc"])
def test_other_users_are_accepted(tmp_path: Path, user: str) -> None:
    path = write(tmp_path, changed(CATALOGUE, "RESTART_GATEWAY.user", user))

    assert load_catalogue(path)["RESTART_GATEWAY"].user == user


@pytest.mark.parametrize(
    "code", ["RESTART_CONTAINER", "RERUN_JOB", "PAUSE_JOB", "UNBLOCK_KANBAN_TASK"]
)
def test_only_the_gateway_restart_may_be_automatic(tmp_path: Path, code: str) -> None:
    assert catalogue_error(tmp_path, f"{code}.auto", True) == (
        f"{code}.auto: this action may not run automatically"
    )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("RESTART_GATEWAY.user", None),
        ("RESTART_GATEWAY.argv", None),
        ("RESTART_CONTAINER.argv", ["hermes"]),
        ("RESTART_CONTAINER.user", "hermes"),
        ("RESTART_CONTAINER.verify", "job_ran"),
        ("RESTART_GATEWAY.verify", "nothing"),
        ("RESTART_GATEWAY.type", "shell"),
        ("RESTART_GATEWAY.label.en", None),
        ("RESTART_GATEWAY.label.de", "x"),
        ("RESTART_GATEWAY.timeout_seconds", 0),
        ("RESTART_GATEWAY.timeout_seconds", 3601),
        ("RESTART_GATEWAY.verify_timeout_seconds", None),
        ("RESTART_GATEWAY.rate_limit", {"auto_per_hour": 2}),
        ("RESTART_GATEWAY.rate_limit", {"auto_per_hour": -1, "manual_per_hour": 5}),
        ("RESTART_GATEWAY.extra", 1),
        ("RERUN_JOB.target", None),
        ("RERUN_JOB.target.must_exist_in", "kanban_task"),
        ("RERUN_JOB.target.must_exist_in", "file"),
        ("RERUN_JOB.target.pattern", "("),
        ("RERUN_JOB.verify", "gateway_restarted"),
        ("PAUSE_JOB.verify", "task_reclaimed"),
        ("lower_case", {"auto": False}),
        ("A B", {}),
        ("NOT_A_TABLE", 3),
    ],
)
def test_incoherent_catalogue_stops_nurse(tmp_path: Path, path: str, value: object) -> None:
    assert catalogue_error(tmp_path, path, value)


def test_limits_can_be_changed_and_french_label_left_out(tmp_path: Path) -> None:
    document = changed(CATALOGUE, "PAUSE_JOB.label", {"en": "Pause"})
    document["PAUSE_JOB"]["rate_limit"] = {"auto_per_hour": 0, "manual_per_hour": 1}

    action: Action = load_catalogue(write(tmp_path, document))["PAUSE_JOB"]

    assert (dict(action.labels), action.manual_per_hour) == ({"en": "Pause"}, 1)


def test_empty_catalogue_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match="empty"):
        load_catalogue(write(tmp_path, {}))


@pytest.mark.parametrize(
    "content", [b"", b"\xff", f"x = {MARKER}".encode(), b"a = 1\na = 2", b"#" * 70000]
)
@pytest.mark.parametrize("load", [load_settings, load_catalogue])
def test_unusable_file_is_refused_without_quoting_it(
    tmp_path: Path, content: bytes, load: Any
) -> None:
    (tmp_path / "file.toml").write_bytes(content)

    with pytest.raises(SettingsError) as caught:
        load(tmp_path / "file.toml")

    assert MARKER not in str(caught.value)
    assert caught.value.__suppress_context__ or caught.value.__context__ is None


@pytest.mark.parametrize("load", [load_settings, load_catalogue])
def test_missing_file_is_refused(tmp_path: Path, load: Any) -> None:
    with pytest.raises(SettingsError, match="cannot read"):
        load(tmp_path / "absent.toml")


@pytest.mark.parametrize(
    ("path", "value"),
    [("RESTART_GATEWAY.user", MARKER + " x"), ("RERUN_JOB.argv", [MARKER])],
)
def test_error_never_quotes_a_value(tmp_path: Path, path: str, value: object) -> None:
    assert MARKER not in catalogue_error(tmp_path, path, value)


def test_action_codes_have_the_same_form_as_in_stetho() -> None:
    assert nurse_settings.ACTION_CODE.pattern == ACTION_CODE.pattern


def test_nurse_uses_the_standard_library_only() -> None:
    for source in (ROOT / "nurse").glob("*.py"):
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                top = name.split(".")[0]
                assert top in sys.stdlib_module_names or top == "nurse", f"{source.name}: {name}"
