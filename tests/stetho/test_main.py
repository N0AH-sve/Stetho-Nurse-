import io
import logging
import signal
import socket
import subprocess
import sys
import threading
import warnings
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import uvicorn
import yaml

from stetho import __main__ as program
from stetho.config import load_config
from stetho.logs import SafeFilter, install_error_hooks, setup_logging
from stetho.web.report import ReportStore

ROOT = Path(__file__).parents[2]
MADE_UP_PHRASE = "made-up-password-long-enough"
MARKER = "made-up-private-text"


@pytest.fixture(autouse=True)
def restored_logging(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    # The program replaces these hooks: put the original ones back afterwards.
    for module, name in ((sys, "excepthook"), (sys, "unraisablehook"), (threading, "excepthook")):
        monkeypatch.setattr(module, name, getattr(module, name))
    yield
    root.handlers, root.level = handlers, level
    logging.captureWarnings(capture=False)


def write_config(tmp_path: Path, **server: Any) -> Path:
    document = yaml.safe_load((ROOT / "config" / "stetho.example.yaml").read_text("utf-8"))
    document["server"] |= {"host": "127.0.0.1", "port": 0} | server
    document["auth"]["password"] = MADE_UP_PHRASE
    # Never the data directory of the machine that runs the tests.
    document["hermes"]["data_dir"] = str(ROOT / "tests" / "fixtures" / "hermes-0.21" / "healthy")
    path = tmp_path / "stetho.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return path


def free_port() -> int:
    with program.listening_socket("127.0.0.1", 0) as free:
        port: int = free.getsockname()[1]
    return port


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
    document["server"]["port"] = free_port()
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

    assert program.main(["--config", str(write_config(tmp_path, port=free_port()))]) == 1

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

    assert program.main(["--config", str(write_config(tmp_path, port=free_port()))]) == code


def test_server_is_hardened(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, port=free_port()))

    options = program.build_server(config, ReportStore()).config

    assert (options.ws, options.http, options.lifespan) == ("none", "h11", "on")
    assert not options.proxy_headers
    assert not options.server_header
    assert not options.access_log
    assert options.log_config is None
    assert options.timeout_graceful_shutdown == 5
    assert options.workers == 1


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


@pytest.mark.parametrize("name", ["WEB_CONCURRENCY", "FORWARDED_ALLOW_IPS"])
def test_environment_variables_change_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setenv(name, MARKER)
    config = load_config(write_config(tmp_path, port=free_port()))

    options = program.build_server(config, ReportStore()).config

    assert (options.workers, options.forwarded_allow_ips) == (1, "")


def test_unexpected_error_at_startup_shows_no_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def crash(_config: object, _store: object) -> None:
        raise RuntimeError(MARKER)

    monkeypatch.setattr(program, "build_server", crash)

    assert program.main(["--config", str(write_config(tmp_path, port=free_port()))]) == 2

    printed = capsys.readouterr().err
    assert "STETHO cannot start: RuntimeError" in printed
    assert MARKER not in printed


def test_unusable_host_name_stops_the_start(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(_host: str, _port: int) -> None:
        raise UnicodeError(MARKER)

    path = write_config(tmp_path, port=free_port())  # before anything is replaced
    monkeypatch.setattr(program, "listening_socket", refuse)

    assert program.main(["--config", str(path)]) == 2
    assert MARKER not in capsys.readouterr().err


@pytest.mark.parametrize("moment", ["startup", "shutdown"])
def test_failed_startup_or_shutdown_is_reported_without_its_message(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    moment: str,
) -> None:
    async def failing_join(*_arguments: object) -> None:
        raise RuntimeError(MARKER)

    def failing_thread(*_arguments: object, **_options: object) -> None:
        raise RuntimeError(MARKER)

    if moment == "startup":
        monkeypatch.setattr("stetho.web.app.threading.Thread", failing_thread)
    else:
        monkeypatch.setattr("stetho.web.app.run_in_threadpool", failing_join)
        # Ask the server to stop as soon as it has started.
        startup = uvicorn.Server.startup

        async def start_then_stop(self: uvicorn.Server, sockets: Any = None) -> None:
            await startup(self, sockets=sockets)
            self.should_exit = True

        monkeypatch.setattr(uvicorn.Server, "startup", start_then_stop)

    assert program.main(["--config", str(write_config(tmp_path, port=free_port()))]) == 1

    printed = capsys.readouterr()
    assert "startup or shutdown failed: RuntimeError" in printed.err
    assert MARKER not in printed.err + printed.out
    assert "Traceback" not in printed.err


@pytest.mark.parametrize("sent", [signal.SIGINT, signal.SIGTERM])
def test_stop_signal_ends_cleanly(tmp_path: Path, sent: signal.Signals) -> None:
    path = write_config(tmp_path, port=free_port())
    process = subprocess.Popen(  # noqa: S603 - this interpreter and fixed arguments
        [sys.executable, "-m", "stetho", "--config", str(path)],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    watchdog = threading.Timer(30, process.kill)
    watchdog.start()
    try:
        assert process.stderr is not None
        started = ""
        for line in process.stderr:
            started += line
            if "STETHO has started" in line:
                break
        process.send_signal(sent)
        out, err = process.communicate()
    finally:
        watchdog.cancel()
        process.kill()

    assert process.returncode == 0, started + err
    assert "STETHO stopped" in err
    assert "Traceback" not in started + err + out


@pytest.mark.parametrize("sent", [signal.SIGINT, signal.SIGTERM])
@pytest.mark.parametrize("moment", ["build_server", "listening_socket", "run"])
def test_stop_signal_before_the_server_serves_ends_cleanly(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    sent: signal.Signals,
    moment: str,
) -> None:
    def interrupted(*_arguments: object, **_options: object) -> None:
        signal.raise_signal(sent)
        pytest.fail("the signal did not interrupt")

    path = write_config(tmp_path, port=free_port())  # before anything is replaced
    target = "stetho.__main__.uvicorn.Server.run" if moment == "run" else None
    monkeypatch.setattr(target or f"stetho.__main__.{moment}", interrupted)
    previous = signal.getsignal(sent)

    code = program.main(["--config", str(path)])

    assert code == 0
    assert "STETHO stopped on request" in capsys.readouterr().err
    assert signal.getsignal(sent) is previous


def test_program_installs_the_error_hooks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail() -> None:
        raise KeyError(MARKER)

    def build_with_a_failing_thread(_config: object, _store: object) -> None:
        thread = threading.Thread(target=fail)
        thread.start()
        thread.join(5)
        raise RuntimeError(MARKER)

    monkeypatch.setattr(program, "build_server", build_with_a_failing_thread)

    program.main(["--config", str(write_config(tmp_path, port=free_port()))])

    printed = capsys.readouterr()
    assert "uncaught error: KeyError" in printed.err
    assert MARKER not in printed.err + printed.out


def test_server_giving_up_is_reported(capsys: pytest.CaptureFixture[str]) -> None:
    class GivesUp:
        should_exit = False

        def run(self, **_options: object) -> None:
            raise SystemExit(3)

    setup_logging()

    server: Any = GivesUp()
    with socket.socket() as unused:
        code = program._serve(server, unused, ReportStore())

    assert code == 1
    assert "the web server could not run" in capsys.readouterr().err


def test_warning_text_is_not_written() -> None:
    stream = io.StringIO()
    with warnings.catch_warnings():
        warnings.simplefilter("always")
        setup_logging(stream)

        warnings.warn(MARKER, stacklevel=1)

        logging.captureWarnings(capture=False)
    assert "message of py.warnings not shown" in stream.getvalue()
    assert MARKER not in stream.getvalue()


def test_uncaught_errors_are_written_without_their_message() -> None:
    stream = io.StringIO()
    setup_logging(stream)
    install_error_hooks()

    def fail() -> None:
        raise KeyError(MARKER)

    thread = threading.Thread(target=fail)
    thread.start()
    thread.join(5)
    sys.excepthook(ValueError, ValueError(MARKER), None)
    sys.unraisablehook(  # what Python calls when an error cannot be raised
        type("Args", (), {"exc_value": OSError(MARKER)})()
    )

    written = stream.getvalue()
    for name in ("KeyError", "ValueError", "OSError"):
        assert f"uncaught error: {name}" in written
    assert MARKER not in written
