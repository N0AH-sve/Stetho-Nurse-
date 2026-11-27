"""Reading of a Hermes file, never modified, with the retries of the anomaly rules."""

import errno
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from stetho.anomalies import AnomalyCode, UnreadableReason

UNREADABLE_DELAYS = (0.2, 0.5, 1.0)  # seconds before each new attempt
PARSE_DELAY = 1.0  # the file may have been in the middle of a write

type Sleep = Callable[[float], None]


class InvalidContentError(Exception):
    """Raised by a parser: the content does not have the expected format."""


@dataclass(frozen=True)
class SourceFailure:
    code: AnomalyCode  # source_missing, source_unreadable or parse_error
    reason: UnreadableReason | None = None  # for source_unreadable


def _read_bytes(path: Path, max_bytes: int, *, tail: bool) -> bytes:
    # No link: it could lead to a secret file. Not blocking: a pipe would hang the reading.
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise OSError(errno.EINVAL, "not a regular file")
        if tail and info.st_size > max_bytes:
            # Keep the last whole lines: the first one read is cut.
            stream.seek(info.st_size - max_bytes - 1)
            return stream.read(max_bytes + 1).partition(b"\n")[2]
        content = stream.read(max_bytes + 1)
    if len(content) > max_bytes:
        if tail:  # the file grew while it was read
            return content[:max_bytes]
        raise OSError(errno.EFBIG, "file too large")
    return content


def read_source[T](
    path: Path,
    parse: Callable[[bytes], T],
    *,
    max_bytes: int,
    sleep: Sleep,
    tail: bool = False,
) -> T | SourceFailure:
    """Read and parse a file. With `tail`, only the end of a large file is read."""
    unreadable_delays = iter(UNREADABLE_DELAYS)
    parse_retried = False
    while True:
        try:
            return parse(_read_bytes(path, max_bytes, tail=tail))
        except (FileNotFoundError, NotADirectoryError):
            return SourceFailure(AnomalyCode.SOURCE_MISSING)
        except OSError as error:
            delay = next(unreadable_delays, None)
            if delay is None:
                reason = (
                    UnreadableReason.PERMISSION_DENIED
                    if isinstance(error, PermissionError)
                    else UnreadableReason.OTHER
                )
                return SourceFailure(AnomalyCode.SOURCE_UNREADABLE, reason)
        except InvalidContentError:
            if parse_retried:
                return SourceFailure(AnomalyCode.PARSE_ERROR)
            parse_retried = True
            delay = PARSE_DELAY
        sleep(delay)
