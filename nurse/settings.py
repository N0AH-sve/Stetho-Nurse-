"""NURSE settings (`nurse.toml`) and action catalogue (`actions.toml`), checked in full.

Anything dangerous or unclear stops NURSE before it does anything. Error messages name the
setting and what is expected, and never quote a value.
"""

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from urllib.parse import urlsplit

_MAX_FILE_BYTES = 64 * 1024
ACTION_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
# The container name becomes part of a path of the Docker API.
_CONTAINER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_USER_NAME = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
_USER_NUMBER = re.compile(r"[0-9]{1,9}")
_PROXY_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,252}")
TARGET_MARK = "{target}"
COMMAND = "hermes"  # the only program an action may start
_MAX_ARGUMENTS = 32
AUTOMATIC_ACTIONS = frozenset({"RESTART_GATEWAY"})  # the only ones that may run by themselves
LANGUAGES = ("en", "fr", "es")
# What proves that an action worked, the kind of target it needs, and what must be true to run it.
VERIFICATIONS: Mapping[str, tuple[str | None, str]] = MappingProxyType(
    {
        "gateway_restarted": (None, "gateway_may_run"),
        "job_ran": ("cron_job", "job_not_running"),
        "job_paused": ("cron_job", "job_enabled"),
        "task_reclaimed": ("kanban_task", "task_running"),
    }
)


class SettingsError(Exception):
    """NURSE must not start. The message is safe to log."""


class _Table:
    """A TOML table whose every key must be read."""

    def __init__(self, value: object, name: str) -> None:
        if not isinstance(value, dict):
            raise SettingsError(f"{name or 'the file'}: expected a table")
        self.name = name
        self._values: dict[str, object] = dict(value)

    def path(self, key: str) -> str:
        shown = key if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", key) else "<unreadable key>"
        return f"{self.name}.{shown}" if self.name else shown

    def has(self, key: str) -> bool:
        return key in self._values

    def take(self, key: str) -> object:
        if key not in self._values:
            raise SettingsError(f"{self.path(key)}: missing")
        return self._values.pop(key)

    def close(self) -> None:
        if self._values:
            unknown = ", ".join(sorted(self.path(key) for key in self._values))
            raise SettingsError(f"unknown setting: {unknown}")

    def table(self, key: str) -> "_Table":
        return _Table(self.take(key), self.path(key))

    def keys(self) -> list[str]:
        return list(self._values)

    def flag(self, key: str) -> bool:
        value = self.take(key)
        if not isinstance(value, bool):
            raise SettingsError(f"{self.path(key)}: expected true or false")
        return value

    def number(self, key: str, lowest: int, highest: int) -> int:
        value = self.take(key)
        if not isinstance(value, int) or isinstance(value, bool) or not lowest <= value <= highest:
            raise SettingsError(f"{self.path(key)}: expected a number from {lowest} to {highest}")
        return value

    def text(self, key: str, pattern: re.Pattern[str] | None = None) -> str:
        value = self.take(key)
        if (
            not isinstance(value, str)
            or not value.strip()
            or "\0" in value
            or (pattern is not None and not pattern.fullmatch(value))
        ):
            raise SettingsError(f"{self.path(key)}: missing or invalid text")
        return value

    def choice(self, key: str, choices: tuple[str, ...]) -> str:
        value = self.take(key)
        if not isinstance(value, str) or value not in choices:
            raise SettingsError(f"{self.path(key)}: expected one of: {', '.join(choices)}")
        return value

    def folder(self, key: str) -> Path:
        value = PurePosixPath(self.text(key))
        if not value.is_absolute() or ".." in value.parts:
            raise SettingsError(f"{self.path(key)}: expected an absolute path without '..'")
        return Path(value)


@dataclass(frozen=True)
class Settings:
    auto_actions: bool
    max_actions_per_hour: int
    container: str
    data_dir: Path
    proxy_url: str
    actions_file: Path
    prescriptions_dir: Path
    results_dir: Path
    log_dir: Path  # private to NURSE: it also keeps what must not be replayed


@dataclass(frozen=True)
class Target:
    kind: str  # `cron_job` or `kanban_task`
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class Action:
    code: str
    labels: Mapping[str, str]
    argv: tuple[str, ...] | None  # None: restart the container
    user: str | None
    target: Target | None
    verify: str
    auto: bool
    auto_per_hour: int
    manual_per_hour: int
    timeout_seconds: int
    verify_timeout_seconds: int

    @property
    def requires(self) -> str:
        """What must be true for the action to make sense now."""
        return VERIFICATIONS[self.verify][1]


def _read_toml(path: Path) -> dict[str, object]:
    try:
        with path.open("rb") as stream:
            content = stream.read(_MAX_FILE_BYTES + 1)
    except OSError as error:
        raise SettingsError(f"cannot read the file: {error.strerror}") from None
    if len(content) > _MAX_FILE_BYTES:
        raise SettingsError("the file is too large")
    try:
        return tomllib.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        # The original message may quote the file.
        raise SettingsError("the file is not valid TOML") from None


def _proxy_url(table: _Table) -> str:
    value = table.text("proxy_url")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        parts, port = urlsplit(""), None
    if (
        parts.scheme != "http"
        or port is None
        or not _PROXY_HOST.fullmatch(parts.hostname or "")
        or value != f"http://{parts.hostname}:{port}"
    ):
        raise SettingsError(f"{table.path('proxy_url')}: expected http://host:port")
    return value


def _check_separate(settings: Settings) -> None:
    """What STETHO can write or read must hold nothing that NURSE trusts."""
    shared = (settings.prescriptions_dir, settings.results_dir)
    folders = (*shared, settings.log_dir, settings.data_dir)
    for index, first in enumerate(folders):
        for second in folders[index + 1 :]:
            if first.is_relative_to(second) or second.is_relative_to(first):
                raise SettingsError("paths: the folders must be separate, none inside another")
    if any(settings.actions_file.is_relative_to(folder) for folder in folders):
        raise SettingsError("paths.actions_file: must be outside the other folders")


def load_settings(path: Path) -> Settings:
    root = _Table(_read_toml(path), "")
    auto_actions = root.flag("auto_actions")
    maximum = root.number("max_actions_per_hour", 1, 100)
    hermes = root.table("hermes")
    container = hermes.text("container", _CONTAINER)
    data_dir = hermes.folder("data_dir")
    hermes.close()
    docker = root.table("docker")
    proxy_url = _proxy_url(docker)
    docker.close()
    paths = root.table("paths")
    settings = Settings(
        auto_actions,
        maximum,
        container,
        data_dir,
        proxy_url,
        actions_file=paths.folder("actions_file"),
        prescriptions_dir=paths.folder("prescriptions_dir"),
        results_dir=paths.folder("results_dir"),
        log_dir=paths.folder("log_dir"),
    )
    paths.close()
    root.close()
    _check_separate(settings)
    return settings


def _user(table: _Table) -> str:
    value = table.text("user")
    number = _USER_NUMBER.fullmatch(value)
    # No `user:group` form, and no way to write root.
    if (number is None and not _USER_NAME.fullmatch(value)) or (
        value == "root" or (number is not None and int(value) == 0)
    ):
        raise SettingsError(f"{table.path('user')}: expected a user name or number, never root")
    return value


def _argv(table: _Table, has_target: bool) -> tuple[str, ...]:
    value = table.take("argv")
    where = table.path("argv")
    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= _MAX_ARGUMENTS
        or not all(isinstance(item, str) and item and "\0" not in item for item in value)
    ):
        raise SettingsError(f"{where}: expected a list of 1 to 32 texts")
    if value[0] != COMMAND:
        # Stricter than a list of shells, which is easy to get around.
        raise SettingsError(f"{where}: the command must be '{COMMAND}'")
    if any(("{" in item or "}" in item) and item != TARGET_MARK for item in value):
        raise SettingsError(f"{where}: {TARGET_MARK} must be a whole argument, with no other brace")
    if value.count(TARGET_MARK) != int(has_target):
        raise SettingsError(f"{where}: {TARGET_MARK} must appear once, and only with a target")
    return tuple(value)


def _target(table: _Table) -> Target | None:
    if not table.has("target"):
        return None
    section = table.table("target")
    kind = section.choice("must_exist_in", ("cron_job", "kanban_task"))
    try:
        pattern = re.compile(section.text("pattern"))
    except (re.error, RecursionError, OverflowError):
        raise SettingsError(f"{section.path('pattern')}: invalid expression") from None
    section.close()
    return Target(kind, pattern)


def _action(code: str, table: _Table) -> Action:
    label = table.table("label")
    labels = {name: label.text(name) for name in LANGUAGES if name == "en" or label.has(name)}
    label.close()
    kind = table.choice("type", ("command", "restart_container"))
    verify = table.choice("verify", tuple(VERIFICATIONS))
    target = _target(table)
    argv = user = None
    if kind == "command":
        argv = _argv(table, target is not None)
        user = _user(table)
    elif verify != "gateway_restarted":
        raise SettingsError(f"{table.path('verify')}: a restart is verified by gateway_restarted")
    wanted = VERIFICATIONS[verify][0]
    if (target.kind if target else None) != wanted:
        raise SettingsError(f"{table.path('target')}: does not match what {verify} verifies")
    auto = table.flag("auto")
    if auto and code not in AUTOMATIC_ACTIONS:
        raise SettingsError(f"{table.path('auto')}: this action may not run automatically")
    auto_per_hour, manual_per_hour = 2, 5
    if table.has("rate_limit"):
        limit = table.table("rate_limit")
        auto_per_hour = limit.number("auto_per_hour", 0, 100)
        manual_per_hour = limit.number("manual_per_hour", 0, 100)
        limit.close()
    action = Action(
        code,
        MappingProxyType(labels),
        argv,
        user,
        target,
        verify,
        auto,
        auto_per_hour,
        manual_per_hour,
        timeout_seconds=table.number("timeout_seconds", 1, 3600),
        verify_timeout_seconds=table.number("verify_timeout_seconds", 1, 3600),
    )
    table.close()
    return action


def load_catalogue(path: Path) -> Mapping[str, Action]:
    """The only actions NURSE will ever accept."""
    root = _Table(_read_toml(path), "")
    actions: dict[str, Action] = {}
    for code in root.keys():  # noqa: SIM118 - not a mapping
        if not ACTION_CODE.fullmatch(code):
            raise SettingsError("an action code must be in capital letters, digits and '_'")
        actions[code] = _action(code, root.table(code))
    if not actions:
        raise SettingsError("the catalogue is empty")
    return MappingProxyType(actions)
