"""What STETHO writes to its log.

The messages of other libraries, such as the web server, and their tracebacks could hold data:
only their origin, their level, and the type and place of an error are written.
"""

import logging
import sys
import threading
from types import TracebackType
from typing import TextIO

from stetho.errors import summarise_error

_OWN = "stetho"


class SafeFilter(logging.Filter):
    """Removes tracebacks from every record, and the message of those that are not ours."""

    def filter(self, record: logging.LogRecord) -> bool:
        own = record.name == _OWN or record.name.startswith(f"{_OWN}.")
        if not own and record.levelno < logging.WARNING:
            return False
        error = record.exc_info[1] if record.exc_info else None
        if not own:
            record.msg = "message of %s not shown"
            record.args = (record.name,)
        if error is not None:
            summary = summarise_error(error)
            calls = "".join(f"\n  {call}" for call in summary.calls)
            record.msg = f"{record.getMessage()}: {summary.error_type}{calls}"
            record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


def setup_logging(stream: TextIO | None = None) -> logging.Handler:
    """Send every log to one filtered handler, on the error output by default."""
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.addFilter(SafeFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    logging.captureWarnings(capture=True)  # a warning could quote a value
    return handler


def _log_uncaught(error: BaseException | None) -> None:
    if error is None:
        logging.getLogger(_OWN).error("uncaught error")
        return
    summary = summarise_error(error)
    calls = "".join(f"\n  {call}" for call in summary.calls)
    logging.getLogger(_OWN).error("uncaught error: %s%s", summary.error_type, calls)


def install_error_hooks() -> None:
    """Python prints the message of an error that nothing catches: write a summary instead."""

    def uncaught(
        _kind: type[BaseException], error: BaseException, _trace: TracebackType | None
    ) -> None:
        _log_uncaught(error)

    def in_thread(args: threading.ExceptHookArgs) -> None:
        if args.exc_type is not SystemExit:
            _log_uncaught(args.exc_value)

    def unraisable(args: "sys.UnraisableHookArgs") -> None:
        _log_uncaught(args.exc_value)

    sys.excepthook = uncaught
    threading.excepthook = in_thread
    sys.unraisablehook = unraisable
