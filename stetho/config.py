"""STETHO settings, read from `stetho.yaml` and checked in full before anything starts.

Error messages name the faulty setting and what is expected. They never quote a value, because
any value may be a misplaced secret.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from ipaddress import IPv6Address
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from stetho.patterns import PROFILE_NAME
from stetho.yamlfile import YamlFileError, load_yaml

_PRINTABLE_KEY = re.compile(r"[A-Za-z0-9_-]{1,40}")
_MAX_PORT = 65535
_MIN_PASSWORD_LENGTH = 16
_MAX_HOST_LENGTH = 253
# A guard rail against values that would break the probe, not an advised rhythm.
_MAX_PROBE_SECONDS = 24 * 60 * 60
_HOST_LABEL = r"[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?"
_HOST_NAME = re.compile(rf"{_HOST_LABEL}(?:\.{_HOST_LABEL})*")
# The thresholds an installation may change, as in the rules of the module.
GATEWAY_THRESHOLDS = (
    "late_after_minutes",
    "down_after_minutes",
    "missing_critical_after_minutes",
)
PLATFORMS_THRESHOLDS = ("critical_after_minutes",)


class ConfigError(Exception):
    """The settings are invalid: STETHO must not start. The message is safe to log."""


class Language(StrEnum):
    EN = "en"
    FR = "fr"
    ES = "es"


class AlertChannel(StrEnum):
    DISCORD = "discord"


@dataclass(frozen=True)
class Secret:
    """A value that must not appear in logs: only `reveal` returns it."""

    _value: str

    def reveal(self) -> str:
        return self._value

    def __bool__(self) -> bool:
        return bool(self._value)

    def __repr__(self) -> str:
        return "Secret(***)"


@dataclass(frozen=True)
class ServerSettings:
    host: str
    port: int
    allowed_hosts: tuple[str, ...]  # lower case; IPv6 addresses between brackets


@dataclass(frozen=True)
class AuthSettings:
    password: Secret  # empty: the reverse proxy handles authentication


@dataclass(frozen=True)
class ProbeSettings:
    interval_seconds: int
    sensor_timeout_seconds: int


@dataclass(frozen=True)
class PathSettings:
    prescriptions_dir: Path
    results_dir: Path
    state_dir: Path


@dataclass(frozen=True)
class HermesSettings:
    data_dir: Path
    profiles: Literal["auto"] | tuple[str, ...]
    profile_labels: Mapping[str, str]


@dataclass(frozen=True)
class ModuleSettings:
    enabled: bool


@dataclass(frozen=True)
class GatewaySettings(ModuleSettings):
    overrides: Mapping[str, int]  # thresholds changed by the installation


@dataclass(frozen=True)
class PlatformsSettings(ModuleSettings):
    overrides: Mapping[str, int]  # thresholds changed by the installation


@dataclass(frozen=True)
class KanbanSettings(ModuleSettings):
    boards: Literal["all"] | tuple[str, ...]


@dataclass(frozen=True)
class SessionsSettings(ModuleSettings):
    refresh_minutes: int
    mask_identities: bool


@dataclass(frozen=True)
class ModulesSettings:
    gateway: GatewaySettings
    platforms: PlatformsSettings
    scheduler: ModuleSettings
    cron_jobs: ModuleSettings
    kanban: KanbanSettings
    sessions: SessionsSettings
    weaknesses: ModuleSettings


@dataclass(frozen=True)
class AlertSettings:
    channel: AlertChannel
    discord_webhook_url: Secret
    debounce_checks: int
    skip_if_hermes_already_alerted: bool


@dataclass(frozen=True)
class Config:
    language: Language
    timezone: ZoneInfo
    server: ServerSettings
    auth: AuthSettings
    probe: ProbeSettings
    paths: PathSettings
    hermes: HermesSettings
    modules: ModulesSettings
    alerts: AlertSettings


def _join(parent: str, key: object) -> str:
    # An unknown key is shown only if it looks like a setting name, not like a pasted value.
    name = key if isinstance(key, str) and _PRINTABLE_KEY.fullmatch(key) else "<unreadable key>"
    return f"{parent}.{name}" if parent else name


class _Section:
    """A mapping of the file. `close` refuses the keys that nothing has read."""

    def __init__(self, value: object, name: str) -> None:
        if not isinstance(value, dict):
            raise ConfigError(f"{name or 'the file'}: expected a list of settings (key: value)")
        self.name = name
        self._values: dict[object, object] = dict(value)

    def path(self, key: object) -> str:
        return _join(self.name, key)

    def unread_keys(self) -> list[object]:
        return list(self._values)

    def has(self, key: str) -> bool:
        return key in self._values

    def take(self, key: str) -> object:
        if key not in self._values:
            raise ConfigError(f"{self.path(key)}: missing setting")
        return self._values.pop(key)

    def close(self) -> None:
        if self._values:
            unknown = ", ".join(sorted(self.path(key) for key in self._values))
            raise ConfigError(f"unknown setting: {unknown}")

    def section(self, key: str) -> "_Section":
        return _Section(self.take(key), self.path(key))

    def boolean(self, key: str) -> bool:
        value = self.take(key)
        if not isinstance(value, bool):
            raise ConfigError(f"{self.path(key)}: expected true or false")
        return value

    def positive_int(self, key: str, maximum: int | None = None) -> int:
        value = self.take(key)
        # bool is a subclass of int: `port: true` must not mean 1.
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 1
            or (maximum is not None and value > maximum)
        ):
            limit = f" from 1 to {maximum}" if maximum is not None else " greater than 0"
            raise ConfigError(f"{self.path(key)}: expected a whole number{limit}")
        return value

    def text(self, key: str, *, allow_empty: bool = False) -> str:
        value = self.take(key)
        if not isinstance(value, str):
            raise ConfigError(f"{self.path(key)}: expected a text, in quotes if needed")
        # A NUL or half of a surrogate pair would fail later, when the value is used.
        if "\0" in value or any("\ud800" <= char <= "\udfff" for char in value):
            raise ConfigError(f"{self.path(key)}: holds a character that cannot be used")
        if not allow_empty and not value.strip():
            raise ConfigError(f"{self.path(key)}: must not be empty")
        return value

    def choice[E: StrEnum](self, key: str, choices: type[E]) -> E:
        value = self.take(key)
        for choice in choices:
            if value == choice.value:
                return choice
        expected = ", ".join(choice.value for choice in choices)
        raise ConfigError(f"{self.path(key)}: expected one of: {expected}")

    def directory(self, key: str) -> Path:
        value = self.text(key)
        if not PurePosixPath(value).is_absolute():
            raise ConfigError(f"{self.path(key)}: expected an absolute path")
        return Path(value)

    def host_names(self, key: str) -> tuple[str, ...]:
        """A non-empty list of host names or addresses, without port or wildcard."""
        value = self.take(key)
        if (
            not isinstance(value, list)
            or not value
            or not all(isinstance(item, str) and _is_host_name(item) for item in value)
            or len({item.lower() for item in value}) != len(value)
        ):
            raise ConfigError(
                f"{self.path(key)}: expected a list of distinct host names or addresses, without "
                "port or wildcard; IPv6 addresses between brackets"
            )
        hosts = tuple(normalise_host(item) for item in value)
        if len(set(hosts)) != len(hosts):
            raise ConfigError(f"{self.path(key)}: the same address is given twice")
        return hosts

    def password(self, key: str) -> Secret:
        """Empty, or long enough."""
        value = self.text(key, allow_empty=True)
        if value and len(value) < _MIN_PASSWORD_LENGTH:
            raise ConfigError(
                f"{self.path(key)}: must be empty or have at least "
                f"{_MIN_PASSWORD_LENGTH} characters"
            )
        return Secret(value)

    def names(self, key: str, wildcard: str) -> tuple[str, ...] | None:
        """A list of distinct folder names, or None for the wildcard word."""
        value = self.take(key)
        if value == wildcard:
            return None
        if (
            not isinstance(value, list)
            or not value
            or not all(isinstance(item, str) and PROFILE_NAME.fullmatch(item) for item in value)
            or len(set(value)) != len(value)
        ):
            raise ConfigError(
                f"{self.path(key)}: expected '{wildcard}' or a list of distinct names "
                "(letters, digits, '_' and '-')"
            )
        return tuple(value)


def normalise_host(value: str) -> str:
    """One written form for a host: lower case, and the short form of an IPv6 address."""
    if value.startswith("[") and value.endswith("]"):
        try:
            return f"[{IPv6Address(value[1:-1]).compressed}]"
        except ValueError:
            pass
    return value.lower()


def _is_host_name(value: str) -> bool:
    if value.startswith("[") and value.endswith("]"):
        address = value[1:-1]
        if "%" in address:  # a zone of a local interface is never in a request
            return False
        try:
            IPv6Address(address)
        except ValueError:
            return False
        return True
    return len(value) <= _MAX_HOST_LENGTH and _HOST_NAME.fullmatch(value) is not None


def _timezone(root: _Section) -> ZoneInfo:
    name = root.text("timezone")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise ConfigError("timezone: unknown time zone, for example Europe/Paris") from None


def _profile_labels(hermes: _Section) -> Mapping[str, str]:
    section = hermes.section("profile_labels")
    labels: dict[str, str] = {}
    for name in section.unread_keys():
        if not isinstance(name, str) or not PROFILE_NAME.fullmatch(name):
            raise ConfigError(f"{section.name}: invalid profile name")
        labels[name] = section.text(name)
    return MappingProxyType(labels)


def _module(modules: _Section, key: str) -> ModuleSettings:
    section = modules.section(key)
    settings = ModuleSettings(enabled=section.boolean("enabled"))
    section.close()
    return settings


def _overrides(section: _Section, thresholds: tuple[str, ...]) -> Mapping[str, int]:
    overrides: dict[str, int] = {}
    if section.has("overrides"):
        changes = section.section("overrides")
        for name in thresholds:
            if changes.has(name):
                overrides[name] = changes.positive_int(name)
        changes.close()
    return MappingProxyType(overrides)


def _gateway(modules: _Section) -> GatewaySettings:
    section = modules.section("gateway")
    enabled = section.boolean("enabled")
    overrides = _overrides(section, GATEWAY_THRESHOLDS)
    section.close()
    return GatewaySettings(enabled=enabled, overrides=overrides)


def _platforms(modules: _Section) -> PlatformsSettings:
    section = modules.section("platforms")
    enabled = section.boolean("enabled")
    overrides = _overrides(section, PLATFORMS_THRESHOLDS)
    section.close()
    return PlatformsSettings(enabled=enabled, overrides=overrides)


def _kanban(modules: _Section) -> KanbanSettings:
    section = modules.section("kanban")
    enabled = section.boolean("enabled")
    boards = section.names("boards", "all")
    section.close()
    return KanbanSettings(enabled=enabled, boards="all" if boards is None else boards)


def _sessions(modules: _Section) -> SessionsSettings:
    section = modules.section("sessions")
    settings = SessionsSettings(
        enabled=section.boolean("enabled"),
        refresh_minutes=section.positive_int("refresh_minutes"),
        mask_identities=section.boolean("mask_identities"),
    )
    section.close()
    return settings


def parse_config(document: object) -> Config:
    """Check a parsed settings file.

    Every setting is required, except `language` and the overrides.
    """
    root = _Section(document, "")
    language = root.choice("language", Language) if root.has("language") else Language.EN
    timezone = _timezone(root)

    section = root.section("server")
    server = ServerSettings(
        host=section.text("host"),
        port=section.positive_int("port", _MAX_PORT),
        allowed_hosts=section.host_names("allowed_hosts"),
    )
    section.close()

    section = root.section("auth")
    auth = AuthSettings(password=section.password("password"))
    section.close()

    section = root.section("probe")
    probe = ProbeSettings(
        interval_seconds=section.positive_int("interval_seconds", _MAX_PROBE_SECONDS),
        sensor_timeout_seconds=section.positive_int("sensor_timeout_seconds", _MAX_PROBE_SECONDS),
    )
    section.close()

    section = root.section("paths")
    paths = PathSettings(
        prescriptions_dir=section.directory("prescriptions_dir"),
        results_dir=section.directory("results_dir"),
        state_dir=section.directory("state_dir"),
    )
    section.close()

    section = root.section("hermes")
    data_dir = section.directory("data_dir")
    profiles = section.names("profiles", "auto")
    hermes = HermesSettings(
        data_dir=data_dir,
        profiles="auto" if profiles is None else profiles,
        profile_labels=_profile_labels(section),
    )
    section.close()

    section = root.section("modules")
    modules = ModulesSettings(
        gateway=_gateway(section),
        platforms=_platforms(section),
        scheduler=_module(section, "scheduler"),
        cron_jobs=_module(section, "cron_jobs"),
        kanban=_kanban(section),
        sessions=_sessions(section),
        weaknesses=_module(section, "weaknesses"),
    )
    section.close()

    section = root.section("alerts")
    channel = section.choice("channel", AlertChannel)
    discord = section.section("discord")
    webhook_url = Secret(discord.text("webhook_url", allow_empty=True))
    discord.close()
    alerts = AlertSettings(
        channel=channel,
        discord_webhook_url=webhook_url,
        debounce_checks=section.positive_int("debounce_checks"),
        skip_if_hermes_already_alerted=section.boolean("skip_if_hermes_already_alerted"),
    )
    section.close()

    root.close()
    return Config(
        language=language,
        timezone=timezone,
        server=server,
        auth=auth,
        probe=probe,
        paths=paths,
        hermes=hermes,
        modules=modules,
        alerts=alerts,
    )


def load_config(path: Path) -> Config:
    """Read and check the settings file, or raise `ConfigError`."""
    try:
        document = load_yaml(path)
    except YamlFileError as error:
        raise ConfigError(str(error)) from None
    return parse_config(document)
