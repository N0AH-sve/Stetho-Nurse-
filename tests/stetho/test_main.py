import io
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import uvicorn
import yaml

from stetho import __main__ as program
from stetho.config import load_config
from stetho.logs import SafeFilter, setup_logging

ROOT = Path(__file__).parents[2]
MADE_UP_PHRASE = "made-up-password-long-enough"
MARKER = "made-up-private-text"


@pytest.fixture(autouse=True)
def restored_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers, root.level = handlers, level
    logging.captureWarnings(capture=False)


def write_config(tmp_path: Path, **server: Any) -> Path:
    document = yaml.safe_load((ROOT / "config" / "stetho.example.yaml").read_text("utf-8"))
    document["server"] |= {"host": "127.0.0.1", "port": 0} | server
    document["auth"]["password"] = MADE_UP_PHRASE
    path = tmp_path / "stetho.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return path


def test_the_settings_file_must_be_given(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stopped:
        program.main([])

    assert stopped.value.code == 2
    assert "--config" in capsys.readouterr().err


def test_invalid_settings_stop_the_start_without_quoting_them(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "stetho.yaml"
    path.write_text(f'language: "{MARKER}"\n', encoding="utf-8")

    assert program.main(["--config", str(path)]) == 2

    printed = capsys.readouterr().err
    assert "STETHO cannot start: language: expected one of" in printed
    assert MARKER not in printed
    assert "Traceback" not in printed


def test_incoherent_thresholds_stop_the_start(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_config(tmp_path, port=1)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["modules"]["gateway"]["overrides"] = {"late_after_minutes": 60}
    path.write_text(yaml.safe_dump(document), encoding="utf-8")

    assert program.main(["--config", str(path)]) == 2
    assert "must be lower than" in capsys.readouterr().err


def test_busy_port_has_its_own_message(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with program.listening_socket("127.0.0.1", 0) as taken:
        port = taken.getsockname()[1]
        path = write_config(tmp_path, port=port)

        assert program.main(["--config", str(path)]) == 2

    printed = capsys.readouterr().err
    assert "cannot listen on the configured host and port" in printed
    assert "Traceback" not in printed


@pytest.mark.parametrize(("password", "warned"), [("", True), (MADE_UP_PHRASE, False)])
def test_missing_password_is_warned_about(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    password: str,
    warned: bool,
) -> None:
    path = write_config(tmp_path, port=1)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["auth"]["password"] = password
    document["server"]["port"] = 65000
    path.write_text(yaml.safe_dump(document), encoding="utf-8")

    def refuse(_host: str, _port: int) -> None:
        raise OSError

    monkeypatch.setattr(program, "listening_socket", refuse)

    program.main(["--config", str(path)])

    printed = capsys.readouterr().err
    assert ("no password is set" in printed) is warned
    assert MADE_UP_PHRASE not in printed


def test_crash_of_the_server_shows_no_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def crash(_self: uvicorn.Server, **_options: object) -> None:
        raise RuntimeError(MARKER)

    monkeypatch.setattr(uvicorn.Server, "run", crash)

    assert program.main(["--config", str(write_config(tmp_path, port=65000))]) == 1

    printed = capsys.readouterr().err
    assert "STETHO stopped: RuntimeError" in printed
    assert MARKER not in printed


@pytest.mark.parametrize(("started", "code"), [(True, 0), (False, 1)])
def test_exit_code_tells_whether_the_server_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, started: bool, code: int
) -> None:
    def run(self: uvicorn.Server, **_options: object) -> None:
        self.started = started

    monkeypatch.setattr(uvicorn.Server, "run", run)

    assert program.main(["--config", str(write_config(tmp_path, port=65000))]) == code


def test_server_is_hardened(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, port=65000))

    options = program.build_server(config).config

    assert (options.ws, options.http, options.lifespan) == ("none", "h11", "on")
    assert not options.proxy_headers
    assert not options.server_header
    assert not options.access_log
    assert options.log_config is None


def logged(name: str, level: int, message: str, **extra: Any) -> str:
    stream = io.StringIO()
    setup_logging(stream)
    logging.getLogger(name).log(level, message, **extra)
    return stream.getvalue()


@pytest.mark.parametrize("name", ["uvicorn.error", "asyncio", "stethoscope", "py.warnings"])
def test_message_of_another_library_is_not_written(name: str) -> None:
    line = logged(name, logging.ERROR, MARKER)

    assert f"ERROR {name}: message of {name} not shown" in line
    assert MARKER not in line


def test_information_of_another_library_is_dropped() -> None:
    assert logged("uvicorn.error", logging.INFO, MARKER) == ""


@pytest.mark.parametrize("name", ["stetho", "stetho.probe"])
def test_own_messages_are_written(name: str) -> None:
    assert "a sentence of STETHO" in logged(name, logging.INFO, "a sentence of STETHO")


@pytest.mark.parametrize("name", ["stetho.web", "uvicorn.error"])
def test_no_traceback_is_ever_written(name: str) -> None:
    def fail() -> None:
        raise KeyError(MARKER)

    try:
        fail()
    except KeyError:
        line = logged(name, logging.ERROR, "failed", exc_info=True, stack_info=True)

    assert "KeyError" in line
    for hidden in (MARKER, "Traceback", "Stack (most recent", "raise KeyError(MARKER)"):
        assert hidden not in line


def test_filter_is_on_the_only_handler() -> None:
    handler = setup_logging(io.StringIO())

    assert logging.getLogger().handlers == [handler]
    assert any(isinstance(item, SafeFilter) for item in handler.filters)
