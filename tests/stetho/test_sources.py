import os
import stat
from collections.abc import Callable
from pathlib import Path

import pytest

from stetho.anomalies import AnomalyCode, UnreadableReason
from stetho.sources import InvalidContentError, SourceFailure, read_source


class Waits:
    """Records the waits instead of sleeping, and may change files meanwhile."""

    def __init__(self, during: Callable[[], None] | None = None) -> None:
        self.delays: list[float] = []
        self.during = during

    def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        if self.during:
            self.during()


def text(content: bytes) -> str:
    if content != b"good":
        raise InvalidContentError
    return content.decode()


def read(path: Path, waits: Waits, max_bytes: int = 100) -> str | SourceFailure:
    return read_source(path, text, max_bytes=max_bytes, sleep=waits)


def test_valid_file_is_read_without_waiting(tmp_path: Path) -> None:
    (tmp_path / "file").write_bytes(b"good")
    waits = Waits()

    assert read(tmp_path / "file", waits) == "good"
    assert waits.delays == []


@pytest.mark.parametrize("name", ["absent", "folder/absent", "file/below"])
def test_missing_file_is_reported_at_once(tmp_path: Path, name: str) -> None:
    (tmp_path / "file").write_bytes(b"good")
    waits = Waits()

    assert read(tmp_path / name, waits) == SourceFailure(AnomalyCode.SOURCE_MISSING)
    assert waits.delays == []


def test_invalid_content_is_read_once_more(tmp_path: Path) -> None:
    (tmp_path / "file").write_bytes(b"bad")
    waits = Waits()

    assert read(tmp_path / "file", waits) == SourceFailure(AnomalyCode.PARSE_ERROR)
    assert waits.delays == [1.0]


def test_content_fixed_meanwhile_is_accepted(tmp_path: Path) -> None:
    path = tmp_path / "file"
    path.write_bytes(b"go")

    def finish_writing() -> None:
        path.write_bytes(b"good")

    assert read(path, Waits(finish_writing)) == "good"


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read every file")
def test_refused_access_is_tried_again_three_times(tmp_path: Path) -> None:
    path = tmp_path / "file"
    path.write_bytes(b"good")
    path.chmod(0)
    waits = Waits()

    assert read(path, waits) == SourceFailure(
        AnomalyCode.SOURCE_UNREADABLE, UnreadableReason.PERMISSION_DENIED
    )
    assert waits.delays == [0.2, 0.5, 1.0]


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read every file")
def test_access_restored_meanwhile_is_accepted(tmp_path: Path) -> None:
    path = tmp_path / "file"
    path.write_bytes(b"good")
    path.chmod(0)

    assert read(path, Waits(lambda: path.chmod(0o600))) == "good"


def folder(path: Path) -> None:
    path.mkdir()


def pipe(path: Path) -> None:
    os.mkfifo(path)  # a plain reading would wait for ever


def link(path: Path) -> None:
    target = path.parent / "target"
    target.write_bytes(b"good")
    path.symlink_to(target)


def too_large(path: Path) -> None:
    path.write_bytes(b"x" * 101)


@pytest.mark.parametrize("make", [folder, pipe, link, too_large])
def test_unusual_file_is_unreadable(tmp_path: Path, make: Callable[[Path], None]) -> None:
    make(tmp_path / "file")

    assert read(tmp_path / "file", Waits()) == SourceFailure(
        AnomalyCode.SOURCE_UNREADABLE, UnreadableReason.OTHER
    )


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"one\ntwo\nthree\n", b"one\ntwo\nthree\n"),  # fits
        (b"x" * 20 + b"\ntwo\nthree\n", b"two\nthree\n"),
        (b"one two three four\nfive\n", b"five\n"),
        (b"x" * 40, b""),
    ],
)
def test_tail_keeps_the_last_whole_lines(tmp_path: Path, content: bytes, expected: bytes) -> None:
    (tmp_path / "log").write_bytes(content)

    result = read_source(tmp_path / "log", bytes, max_bytes=14, sleep=Waits(), tail=True)

    assert result == expected


def test_tail_keeps_a_line_that_starts_at_the_limit(tmp_path: Path) -> None:
    (tmp_path / "log").write_bytes(b"first\nsecond\n")

    result = read_source(tmp_path / "log", bytes, max_bytes=7, sleep=Waits(), tail=True)

    assert result == b"second\n"


def test_log_that_grows_while_read_stays_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "log").write_bytes(b"x" * 30)
    real = os.fstat

    def small_at_first(descriptor: int) -> os.stat_result:
        values = list(real(descriptor))
        values[stat.ST_SIZE] = 5
        return os.stat_result(values)

    monkeypatch.setattr(os, "fstat", small_at_first)

    result = read_source(tmp_path / "log", bytes, max_bytes=14, sleep=Waits(), tail=True)

    assert result == b"x" * 14


def test_file_is_never_modified(tmp_path: Path) -> None:
    path = tmp_path / "file"
    path.write_bytes(b"good")
    before = path.stat()

    read(path, Waits())

    after = path.stat()
    assert path.read_bytes() == b"good"
    assert (after.st_mtime_ns, after.st_mode) == (before.st_mtime_ns, before.st_mode)
