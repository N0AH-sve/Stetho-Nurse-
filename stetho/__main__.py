"""Starts STETHO: `python -m stetho --config <file>`."""

import argparse
import contextlib
import logging
import signal
import socket
import threading
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType

import uvicorn

from stetho.config import Config, ConfigError, load_config
from stetho.errors import summarise_error
from stetho.i18n import TranslationError, load_translator
from stetho.logs import install_error_hooks, setup_logging
from stetho.modules.gateway.rules import RulesError
from stetho.probe import Probe, build_sensors
from stetho.web.app import ReportStore, create_app

_LOG = logging.getLogger("stetho")
_CANNOT_START = 2
_FAILED = 1


def build_server(config: Config, store: ReportStore) -> uvicorn.Server:
    """Everything that can refuse to start does so here, before anything is served."""
    translator = load_translator(config.language)
    probe = Probe(
        build_sensors(config, translator),
        translator,
        timeout_seconds=config.probe.sensor_timeout_seconds,
        clock=lambda: datetime.now(UTC),
    )
    app = create_app(config, translator, probe, store)
    return uvicorn.Server(
        uvicorn.Config(
            app,
            http="h11",
            ws="none",
            lifespan="on",
            proxy_headers=False,  # X-Forwarded-* is ignored
            # Given here so that the web server reads no environment variable.
            workers=1,
            forwarded_allow_ips="",
            server_header=False,
            access_log=False,
            log_config=None,  # keeps the filtered log of STETHO
            timeout_graceful_shutdown=5,
        )
    )


def listening_socket(host: str, port: int) -> socket.socket:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    return socket.create_server((host, port), family=family)


@contextlib.contextmanager
def _stop_signals(server: uvicorn.Server) -> Iterator[None]:
    """Make SIGINT and SIGTERM end STETHO without a traceback.

    The web server handles them while it serves, then sends them again: by then it is already
    stopping, and nothing more is done. Before that, they interrupt the startup.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def handle(_signal: int, _frame: FrameType | None) -> None:
        if not server.should_exit:
            raise KeyboardInterrupt

    previous = {name: signal.signal(name, handle) for name in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield
    finally:
        for name, handler in previous.items():
            signal.signal(name, handler)


def _log_error(sentence: str, error: BaseException) -> None:
    summary = summarise_error(error)
    calls = "".join(f"\n  {call}" for call in summary.calls)
    _LOG.error("%s: %s%s", sentence, summary.error_type, calls)


def _serve(server: uvicorn.Server, listener: socket.socket, store: ReportStore) -> int:
    try:
        with _stop_signals(server):
            server.run(sockets=[listener])
    except KeyboardInterrupt:
        _LOG.info("STETHO stopped on request")
        return 0
    except SystemExit:
        # How the web server gives up, for example when the startup fails.
        _LOG.error("STETHO stopped: the web server could not run")
        return _FAILED
    except Exception as error:  # noqa: BLE001 - Python would print the message
        _log_error("STETHO stopped", error)
        return _FAILED
    if store.failed or not server.started:
        return _FAILED
    _LOG.info("STETHO stopped")
    return 0


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m stetho", description="Watches Hermes.")
    parser.add_argument("--config", required=True, type=Path, help="the settings file")
    options = parser.parse_args(arguments)
    setup_logging()
    install_error_hooks()
    store = ReportStore()
    try:
        config = load_config(options.config)
        server = build_server(config, store)
    except (ConfigError, TranslationError, RulesError) as error:
        _LOG.error("STETHO cannot start: %s", error)
        return _CANNOT_START
    except Exception as error:  # noqa: BLE001 - Python would print the message
        _log_error("STETHO cannot start", error)
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
    try:
        return _serve(server, listener, store)
    finally:
        listener.close()


if __name__ == "__main__":
    raise SystemExit(main())
