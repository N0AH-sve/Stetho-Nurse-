"""Starts NURSE: `python -m nurse --config <file>`."""

import argparse
import logging
import signal
import threading
from collections.abc import Sequence
from pathlib import Path
from types import FrameType

from nurse.reception import Reception
from nurse.settings import SettingsError, load_catalogue, load_settings

_LOG = logging.getLogger("nurse")
_CANNOT_START = 2
_INTERVAL_SECONDS = 1.0


def watch(reception: Reception, stop: threading.Event) -> None:
    failing = False
    while not stop.is_set():
        try:
            reception.run_once()
            failing = False
        except Exception as error:  # noqa: BLE001 - Python would print the message
            if not failing:  # said once, not every second
                _LOG.error("the requests cannot be handled: %s", type(error).__name__)
            failing = True
        stop.wait(_INTERVAL_SECONDS)


def main(arguments: Sequence[str] | None = None, stop: threading.Event | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m nurse", description="Checks requests.")
    parser.add_argument("--config", required=True, type=Path, help="the settings file")
    options = parser.parse_args(arguments)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        settings = load_settings(options.config)
        actions = load_catalogue(settings.actions_file)
    except SettingsError as error:
        _LOG.error("NURSE cannot start: %s", error)
        return _CANNOT_START
    stopping = stop or threading.Event()

    def handle(_signal: int, _frame: FrameType | None) -> None:
        stopping.set()

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, handle)
        signal.signal(signal.SIGTERM, handle)
    _LOG.warning("NURSE checks requests but runs no action yet")
    watch(Reception(settings, actions), stopping)
    _LOG.info("NURSE stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
