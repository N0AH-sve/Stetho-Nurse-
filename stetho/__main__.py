"""Starts STETHO: `python -m stetho --config <file>`."""

import argparse
import logging
import socket
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import uvicorn

from stetho.config import Config, ConfigError, load_config
from stetho.errors import summarise_error
from stetho.i18n import TranslationError, load_translator
from stetho.logs import setup_logging
from stetho.modules.gateway.rules import RulesError
from stetho.probe import Probe, build_sensors
from stetho.web.app import ReportStore, create_app

_LOG = logging.getLogger("stetho")
_CANNOT_START = 2
_FAILED = 1


def build_server(config: Config) -> uvicorn.Server:
    """Everything that can refuse to start does so here, before anything is served."""
    translator = load_translator(config.language)
    probe = Probe(
        build_sensors(config, translator),
        translator,
        timeout_seconds=config.probe.sensor_timeout_seconds,
        clock=lambda: datetime.now(UTC),
    )
    app = create_app(config, translator, probe, ReportStore())
    return uvicorn.Server(
        uvicorn.Config(
            app,
            http="h11",
            ws="none",
            lifespan="on",
            proxy_headers=False,  # X-Forwarded-* is ignored
            server_header=False,
            access_log=False,
            log_config=None,  # keeps the filtered log of STETHO
            timeout_graceful_shutdown=5,
        )
    )


def listening_socket(host: str, port: int) -> socket.socket:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    return socket.create_server((host, port), family=family)


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m stetho", description="Watches Hermes.")
    parser.add_argument("--config", required=True, type=Path, help="the settings file")
    options = parser.parse_args(arguments)
    setup_logging()
    try:
        config = load_config(options.config)
        server = build_server(config)
    except (ConfigError, TranslationError, RulesError) as error:
        _LOG.error("STETHO cannot start: %s", error)
        return _CANNOT_START
    if not config.auth.password:
        _LOG.warning(
            "no password is set: the reverse proxy must authenticate, "
            "and whoever reaches the port has access"
        )
    try:
        # Opened here: the message of the web server about this failure is not shown.
        listener = listening_socket(config.server.host, config.server.port)
    except (OSError, UnicodeError):
        _LOG.error(
            "STETHO cannot start: it cannot listen on the configured host and port "
            "(is the port already used?)"
        )
        return _CANNOT_START
    _LOG.info("STETHO is listening")
    try:
        server.run(sockets=[listener])
    except Exception as error:  # noqa: BLE001 - Python would print the message
        summary = summarise_error(error)
        calls = "".join(f"\n  {call}" for call in summary.calls)
        _LOG.error("STETHO stopped: %s%s", summary.error_type, calls)
        return _FAILED
    finally:
        listener.close()
    return 0 if server.started else _FAILED


if __name__ == "__main__":
    raise SystemExit(main())
