"""What may be kept of an unexpected exception: its type and where it happened in STETHO.

The message of an exception can copy data read from Hermes, so it is never read. Only frames of
STETHO's own code are kept: other paths belong to the installation.
"""

import re
import traceback
from dataclasses import dataclass
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
_TYPE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,99}")


@dataclass(frozen=True)
class Call:
    file: str  # relative to the folder that holds the package, such as `stetho/results.py`
    line: int
    function: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line} ({self.function})"


@dataclass(frozen=True)
class ErrorSummary:
    error_type: str
    calls: tuple[Call, ...]  # outermost first; the last one is closest to the error

    def __str__(self) -> str:
        """The short form shown with the anomaly."""
        return f"{self.error_type} · {self.calls[-1]}" if self.calls else self.error_type


def summarise_error(error: BaseException, package_dir: Path = PACKAGE_DIR) -> ErrorSummary:
    name = type(error).__name__
    calls: list[Call] = []
    for frame, line in traceback.walk_tb(error.__traceback__):
        path = Path(frame.f_code.co_filename)
        if path.is_relative_to(package_dir):
            file = path.relative_to(package_dir.parent).as_posix()
            calls.append(Call(file, line, frame.f_code.co_name))
    return ErrorSummary(name if _TYPE_NAME.fullmatch(name) else "Exception", tuple(calls))
