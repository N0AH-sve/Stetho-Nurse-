import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from stetho.config import AlertChannel, Config, ConfigError, Language, load_config, parse_config

EXAMPLE = Path(__file__).parents[2] / "config" / "stetho.example.yaml"
MARKER = "made-up-secret-value"


def example() -> dict[str, Any]:
    document = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return copy.deepcopy(document)


def changed(path: str, value: object) -> dict[str, Any]:
    document = example()
    *parents, key = path.split(".")
    target = document
    for parent in parents:
        target = target[parent]
    target[key] = value
    return document


def error_of(document: object) -> str:
    with pytest.raises(ConfigError) as caught:
        parse_config(document)
    return str(caught.value)


def write(tmp_path: Path, content: str | bytes) -> Path:
    path = tmp_path / "stetho.yaml"
    path.write_bytes(content.encode() if isinstance(content, str) else content)
    return path


def load_error(path: Path) -> str:
    with pytest.raises(ConfigError) as caught:
        load_config(path)
    # No chained exception may be printed: it could quote the file.
    assert caught.value.__suppress_context__ or caught.value.__context__ is None
    return str(caught.value)


def test_example_file_is_valid() -> None:
    config = load_config(EXAMPLE)

    assert config.language is Language.EN
    assert config.timezone.key == "Europe/Paris"
    assert config.server.port == 8090
    assert config.hermes.data_dir == Path("/hermes")
    assert config.hermes.profiles == "auto"
    assert config.hermes.profile_labels == {"default": "Main"}
    assert config.modules.kanban.boards == "all"
    assert config.modules.sessions.refresh_minutes == 5
    assert config.alerts.channel is AlertChannel.DISCORD
    assert not config.auth.password


def test_language_defaults_to_english() -> None:
    document = example()
    del document["language"]

    assert parse_config(document).language is Language.EN


@pytest.mark.parametrize("language", list(Language))
def test_every_supported_language_is_accepted(language: Language) -> None:
    assert parse_config(changed("language", language.value)).language is language


def test_lists_of_names_are_accepted() -> None:
    document = changed("hermes.profiles", ["default", "support_2"])
    document["modules"]["kanban"]["boards"] = ["release-board"]

    config = parse_config(document)

    assert config.hermes.profiles == ("default", "support_2")
    assert config.modules.kanban.boards == ("release-board",)


@pytest.mark.parametrize(
    ("hosts", "expected"),
    [
        (["localhost"], ("localhost",)),
        (["Status.Example.COM", "127.0.0.1"], ("status.example.com", "127.0.0.1")),
        (["[::1]", "[FD00::1]"], ("[::1]", "[fd00::1]")),
        (["stetho_web"], ("stetho_web",)),
    ],
)
def test_host_names_are_accepted_in_lower_case(hosts: list[str], expected: tuple[str, ...]) -> None:
    assert parse_config(changed("server.allowed_hosts", hosts)).server.allowed_hosts == expected


@pytest.mark.parametrize(
    "hosts",
    [
        [],
        "localhost",
        None,
        [1],
        [""],
        ["*"],
        ["*.example.com"],
        ["localhost:8090"],
        ["http://localhost"],
        ["example.com/path"],
        ["example.com."],
        [" localhost"],
        ["localhost\n"],
        ["-name"],
        ["a..b"],
        ["a" * 64],
        [".".join(["abcdefg"] * 40)],
        ["::1"],
        ["[::1]:80"],
        ["[not-an-address]"],
        ["[127.0.0.1]"],
        ["[fe80::1%eth0]"],
        ["localhost", "LOCALHOST"],
    ],
)
def test_other_host_names_are_refused(hosts: object) -> None:
    message = error_of(changed("server.allowed_hosts", hosts))

    assert message.startswith("server.allowed_hosts: expected")


@pytest.mark.parametrize("password", ["", "a" * 16, "é" * 16, "with : colons and spaces"])
def test_empty_or_long_password_is_accepted(password: str) -> None:
    assert parse_config(changed("auth.password", password)).auth.password.reveal() == password


@pytest.mark.parametrize("password", ["a", "a" * 15, "é" * 15])
def test_short_password_is_refused_without_quoting_it(password: str) -> None:
    message = error_of(changed("auth.password", password))

    assert message == "auth.password: must be empty or have at least 16 characters"


@pytest.mark.parametrize(
    "path", ["server.host", "auth.password", "hermes.data_dir", "alerts.discord.webhook_url"]
)
@pytest.mark.parametrize("bad", ["\0", "\udcff"])
def test_unusable_character_is_refused_without_quoting(path: str, bad: str) -> None:
    message = error_of(changed(path, f"/{MARKER}-long-enough{bad}"))

    assert message == f"{path}: holds a character that cannot be used"


def test_same_address_written_twice_is_refused() -> None:
    document = changed("server.allowed_hosts", ["[::1]", "[0:0:0:0:0:0:0:1]"])

    assert error_of(document) == "server.allowed_hosts: the same address is given twice"


@pytest.mark.parametrize("name", ["interval_seconds", "sensor_timeout_seconds"])
@pytest.mark.parametrize("value", [1, 86400])
def test_probe_duration_within_a_day_is_accepted(name: str, value: int) -> None:
    config = parse_config(changed(f"probe.{name}", value))

    assert getattr(config.probe, name) == value


@pytest.mark.parametrize("name", ["interval_seconds", "sensor_timeout_seconds"])
@pytest.mark.parametrize("value", [0, 86401, 10**12])
def test_probe_duration_outside_a_day_is_refused(name: str, value: int) -> None:
    assert error_of(changed(f"probe.{name}", value)) == (
        f"probe.{name}: expected a whole number from 1 to 86400"
    )


def test_platform_threshold_may_be_changed() -> None:
    document = changed("modules.platforms.overrides", {"critical_after_minutes": 10})

    assert parse_config(document).modules.platforms.overrides == {"critical_after_minutes": 10}
    assert parse_config(example()).modules.platforms.overrides == {}


@pytest.mark.parametrize("value", [{"critical_after_minutes": 0}, {"other": 1}, []])
def test_invalid_platform_override_is_refused(value: object) -> None:
    message = error_of(changed("modules.platforms.overrides", value))

    assert "modules.platforms.overrides" in message


def test_gateway_thresholds_may_be_changed() -> None:
    document = changed("modules.gateway.overrides", {"down_after_minutes": 10})

    assert parse_config(document).modules.gateway.overrides == {"down_after_minutes": 10}
    assert parse_config(example()).modules.gateway.overrides == {}


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ({"down_after_minutes": 0}, "modules.gateway.overrides.down_after_minutes: expected"),
        ({"down_after_minutes": "5"}, "modules.gateway.overrides.down_after_minutes: expected"),
        ({"down_after": 5}, "unknown setting: modules.gateway.overrides.down_after"),
        ([], "modules.gateway.overrides: expected"),
        (None, "modules.gateway.overrides: expected"),
    ],
)
def test_invalid_gateway_override_is_refused(value: object, message: str) -> None:
    assert error_of(changed("modules.gateway.overrides", value)).startswith(message)


def test_settings_cannot_be_modified() -> None:
    config = parse_config(example())

    with pytest.raises(TypeError):
        config.hermes.profile_labels["default"] = "other"  # type: ignore[index]


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("language", "de"),
        ("language", None),
        ("timezone", "Mars/Olympus"),
        ("timezone", "../etc/passwd"),
        ("timezone", "Europe"),
        ("server.host", ""),
        ("server.port", 0),
        ("server.port", 65536),
        ("server.port", "8090"),
        ("server.port", True),
        ("auth.password", None),
        ("auth.password", 1234),
        ("probe.interval_seconds", 0),
        ("probe.sensor_timeout_seconds", 1.5),
        ("paths.state_dir", "state"),
        ("paths.results_dir", ""),
        ("hermes.data_dir", "/her\0mes"),
        ("hermes.profiles", "all"),
        ("hermes.profiles", []),
        ("hermes.profiles", ["../escape"]),
        ("hermes.profiles", [".hidden"]),
        ("hermes.profiles", ["a", "a"]),
        ("hermes.profiles", [1]),
        ("hermes.profile_labels", {"a/b": "Label"}),
        ("hermes.profile_labels", {"default": ""}),
        ("modules.gateway.enabled", "yes"),
        ("modules.kanban.boards", ["a b"]),
        ("modules.sessions.refresh_minutes", -1),
        ("alerts.channel", "telegram"),
        ("alerts.debounce_checks", 0),
        ("alerts.skip_if_hermes_already_alerted", 1),
        ("alerts.discord", "not a section"),
    ],
)
def test_invalid_value_is_refused_and_named(path: str, value: object) -> None:
    assert error_of(changed(path, value)).startswith(path)


@pytest.mark.parametrize(
    "path",
    [
        "timezone",
        "server",
        "auth.password",
        "server.allowed_hosts",
        "probe.interval_seconds",
        "paths.state_dir",
        "hermes.profile_labels",
        "modules.weaknesses",
        "modules.kanban.boards",
        "alerts.discord.webhook_url",
    ],
)
def test_missing_setting_is_refused(path: str) -> None:
    document = example()
    *parents, key = path.split(".")
    target = document
    for parent in parents:
        target = target[parent]
    del target[key]

    assert error_of(document) == f"{path}: missing setting"


@pytest.mark.parametrize(
    "path",
    [
        "extra",
        "server.hots",
        "auth.pasword",
        "hermes.profile",
        "modules.unknown_module",
        "modules.gateway.enable",
        "modules.cron_jobs.overrides",
        "modules.kanban.overrides",
        "alerts.telegram",
        "alerts.discord.webhook",
    ],
)
def test_unknown_setting_is_refused(path: str) -> None:
    assert error_of(changed(path, True)) == f"unknown setting: {path}"


@pytest.mark.parametrize("document", [None, [], "text", 3])
def test_file_that_is_not_a_mapping_is_refused(document: object) -> None:
    assert error_of(document).startswith("the file")


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("language", MARKER),
        ("timezone", MARKER),
        ("server.port", MARKER),
        ("auth.password", [MARKER]),
        ("auth", MARKER),
        ("paths.state_dir", MARKER),
        ("hermes.profiles", [f"{MARKER}/x"]),
        ("alerts.channel", MARKER),
        ("alerts.discord", MARKER),
    ],
)
def test_error_never_quotes_a_value(path: str, value: object) -> None:
    assert MARKER not in error_of(changed(path, value))


def test_unreadable_key_name_is_not_quoted() -> None:
    # `{password:value}` without a space is read by YAML as a single key.
    message = error_of(changed("auth", {f"password:{MARKER}": None, "password": ""}))

    assert message == "unknown setting: auth.<unreadable key>"


def test_secrets_are_hidden_when_settings_are_printed() -> None:
    document = changed("auth.password", MARKER)
    document["alerts"]["discord"]["webhook_url"] = f"https://example.invalid/{MARKER}"

    config: Config = parse_config(document)

    assert MARKER not in repr(config)
    assert MARKER not in str(config.auth.password)
    assert config.auth.password.reveal() == MARKER
    assert config.alerts.discord_webhook_url.reveal().endswith(MARKER)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (f'auth:\n  password: "{MARKER}\n', "invalid YAML at line"),
        (f"auth:\n  password: {MARKER}: x\n", "invalid YAML at line 2"),
        (f"password: {MARKER}\npassword: other\n", "repeated key at line 2"),
        (f"password: *{MARKER}\n", "invalid YAML at line 1"),
        ("a: 1\n---\nb: 2\n", "invalid YAML"),
        ("when: 2026-13-45\n", "invalid YAML value"),
        ("a: " + "[" * 5000 + "]" * 5000, "invalid YAML value or structure"),
        ("? [a, b]\n: 1\n", "invalid YAML"),
        (f"a: {MARKER}\x07\n", "invalid YAML"),
    ],
)
def test_broken_yaml_is_refused_without_quoting_it(
    tmp_path: Path, content: str, message: str
) -> None:
    error = load_error(write(tmp_path, content))

    assert error.startswith(message)
    assert MARKER not in error


def test_yaml_cannot_build_python_objects(tmp_path: Path) -> None:
    path = write(tmp_path, "a: !!python/object/apply:os.getcwd []\n")

    assert load_error(path).startswith("invalid YAML")


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"", "the file: expected"),
        (b"\xff\xfe", "the file is not valid UTF-8"),
        (b"#" * (256 * 1024 + 1), "the file is larger than"),
    ],
)
def test_unusable_file_is_refused(tmp_path: Path, content: bytes, message: str) -> None:
    assert load_error(write(tmp_path, content)).startswith(message)


def test_missing_file_is_refused(tmp_path: Path) -> None:
    assert load_error(tmp_path / "absent.yaml").startswith("cannot read the file")


def test_directory_instead_of_file_is_refused(tmp_path: Path) -> None:
    assert load_error(tmp_path).startswith("cannot read the file")
