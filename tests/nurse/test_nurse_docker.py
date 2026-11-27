import io
import json
import socket
import threading
from collections.abc import Callable, Iterator
from contextlib import suppress
from types import SimpleNamespace

import pytest

from nurse.docker import (
    OUTPUT_BYTES,
    Docker,
    DockerError,
    ExecState,
    Failure,
    Output,
    _read_output,
)

EXEC_ID = "a" * 64
Reply = Callable[[socket.socket], None]


class FakeDocker:
    """A server that answers one request with the bytes that a test chooses."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, str], bytes]] = []
        self.reply: Reply = lambda _: None
        self.release = threading.Event()
        self._server = socket.create_server(("127.0.0.1", 0))
        self.port = self._server.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        with suppress(OSError):
            while True:
                client, _ = self._server.accept()
                with client, suppress(OSError):
                    self._read(client)
                    self.reply(client)

    def _read(self, client: socket.socket) -> None:
        received = b""
        while b"\r\n\r\n" not in received:
            received += self._receive(client)
        head, _, body = received.partition(b"\r\n\r\n")
        first, *lines = head.decode().split("\r\n")
        headers = dict(line.split(": ", 1) for line in lines)
        while len(body) < int(headers.get("Content-Length", 0)):
            body += self._receive(client)
        self.requests.append((first, headers, body))

    @staticmethod
    def _receive(client: socket.socket) -> bytes:
        data = client.recv(4096)
        if not data:
            raise ConnectionError
        return data

    def close(self) -> None:
        self.release.set()
        with suppress(OSError):
            self._server.shutdown(socket.SHUT_RDWR)  # wakes the waiting server
        self._server.close()
        self._thread.join(5)

    def client(self) -> Docker:
        return Docker(f"http://127.0.0.1:{self.port}", "hermes")


@pytest.fixture
def fake() -> Iterator[FakeDocker]:
    server = FakeDocker()
    yield server
    server.close()


def answer(status: int, body: bytes = b"", *headers: str) -> Reply:
    head = [f"HTTP/1.1 {status} X", "Connection: close", *headers]
    return lambda client: client.sendall("\r\n".join(head).encode() + b"\r\n\r\n" + body)


def document(status: int, value: object) -> Reply:
    body = json.dumps(value).encode()
    return answer(status, body, f"Content-Length: {len(body)}")


def frame(stream: int, data: bytes) -> bytes:
    return bytes([stream, 0, 0, 0]) + len(data).to_bytes(4, "big") + data


def failure_of(call: Callable[[], object]) -> DockerError:
    with pytest.raises(DockerError) as raised:
        call()
    return raised.value


def test_command_is_prepared_without_privilege_for_the_user(fake: FakeDocker) -> None:
    fake.reply = document(201, {"Id": EXEC_ID})

    assert fake.client().create_exec(["hermes", "gateway", "restart"], "hermes", 5) == EXEC_ID

    first, headers, body = fake.requests[0]
    assert first == "POST /containers/hermes/exec HTTP/1.1"
    assert headers["Content-Type"] == "application/json"
    assert json.loads(body) == {
        "AttachStdin": False,
        "AttachStdout": True,
        "AttachStderr": True,
        "Tty": False,
        "Privileged": False,
        "User": "hermes",
        "Cmd": ["hermes", "gateway", "restart"],
    }


@pytest.mark.parametrize(
    "value", [{}, {"Id": 1}, {"Id": "../x"}, {"Id": "A" * 64}, {"Id": "a" * 65}, []]
)
def test_identifier_that_could_change_a_path_is_refused(fake: FakeDocker, value: object) -> None:
    fake.reply = document(201, value)

    error = failure_of(lambda: fake.client().create_exec(["hermes"], "hermes", 5))

    assert error.failure is Failure.ANSWER


@pytest.mark.parametrize(
    "body",
    [
        b"{",
        b"[" * 60_000,  # under the size limit, and too deep
        json.dumps({"Running": False, "ExitCode": 0, "other": "x" * 70_000}).encode(),
    ],
)
def test_unreadable_or_too_long_answer_is_refused(fake: FakeDocker, body: bytes) -> None:
    fake.reply = answer(200, body)

    error = failure_of(lambda: fake.client().inspect_exec(EXEC_ID, 5))

    assert error.failure is Failure.ANSWER


def test_output_streams_are_kept_apart(fake: FakeDocker) -> None:
    body = frame(1, b"Ran now: ") + frame(2, b"warning\n") + frame(1, b"succeeded.\n")
    fake.reply = answer(200, body)

    output = fake.client().run_exec(EXEC_ID, 5)

    assert output == Output(b"Ran now: succeeded.\n", b"warning\n", False, True)
    first, _, sent = fake.requests[0]
    assert first == f"POST /exec/{EXEC_ID}/start HTTP/1.1"
    assert json.loads(sent) == {"Detach": False, "Tty": False}


def test_frames_cut_anywhere_are_put_together(fake: FakeDocker) -> None:
    body = frame(1, b"first\n") + frame(1, b"") + frame(2, b"second\n")

    def drop_by_drop(client: socket.socket) -> None:
        answer(200)(client)
        for byte in body:
            client.sendall(bytes([byte]))

    fake.reply = drop_by_drop

    assert fake.client().run_exec(EXEC_ID, 5) == Output(b"first\n", b"second\n", False, True)


def test_only_the_last_whole_lines_of_a_long_output_are_kept(fake: FakeDocker) -> None:
    long_name = b"x" * (3 * OUTPUT_BYTES) + b"Reclaimed t_00000000\n"
    fake.reply = answer(200, frame(1, long_name) + frame(1, b"the end\n") + frame(2, b"short"))

    output = fake.client().run_exec(EXEC_ID, 5)

    # What is left of the long line must not pass for a whole one.
    assert output == Output(b"the end\n", b"short", True, True)


def test_long_output_without_any_line_end_is_dropped(fake: FakeDocker) -> None:
    fake.reply = answer(200, frame(1, b"x" * (2 * OUTPUT_BYTES)))

    assert fake.client().run_exec(EXEC_ID, 5) == Output(b"", b"", True, True)


def test_frames_read_one_byte_at_a_time_are_put_together() -> None:
    body = io.BytesIO(frame(1, b"first\n") + frame(2, b"second\n"))
    response = SimpleNamespace(read1=lambda _: body.read(1), length=None)

    output = _read_output(response)  # type: ignore[arg-type]  # only these two are used

    assert output == Output(b"first\n", b"second\n", False, True)


@pytest.mark.parametrize("cut", [3, 10])
def test_output_that_stops_inside_a_frame_is_incomplete(fake: FakeDocker, cut: int) -> None:
    fake.reply = answer(200, frame(1, b"done\n") + frame(1, b"more\n")[:cut])

    assert not fake.client().run_exec(EXEC_ID, 5).complete


@pytest.mark.parametrize("header", [b"\x03\0\0\0\0\0\0\x01x", b"\x01\x01\0\0\0\0\0\x01x"])
def test_output_that_is_not_made_of_frames_is_refused(fake: FakeDocker, header: bytes) -> None:
    fake.reply = answer(200, header)

    assert failure_of(lambda: fake.client().run_exec(EXEC_ID, 5)).failure is Failure.ANSWER


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"Running": False, "ExitCode": 0}, ExecState(False, 0)),
        ({"Running": False, "ExitCode": 1}, ExecState(False, 1)),
        ({"Running": False, "ExitCode": None}, ExecState(False, None)),
        ({"Running": True, "ExitCode": 0}, ExecState(True, None)),
    ],
)
def test_exit_code_counts_only_once_the_command_ended(
    fake: FakeDocker, value: object, expected: ExecState
) -> None:
    fake.reply = document(200, value)

    assert fake.client().inspect_exec(EXEC_ID, 5) == expected
    assert fake.requests[0][0] == f"GET /exec/{EXEC_ID}/json HTTP/1.1"


@pytest.mark.parametrize(
    "value", [{}, {"Running": 0, "ExitCode": 0}, {"Running": False, "ExitCode": True}]
)
def test_unexpected_state_of_a_command_is_refused(fake: FakeDocker, value: object) -> None:
    fake.reply = document(200, value)

    assert failure_of(lambda: fake.client().inspect_exec(EXEC_ID, 5)).failure is Failure.ANSWER


def test_container_is_restarted(fake: FakeDocker) -> None:
    fake.reply = answer(204)

    fake.client().restart_container(5)

    assert fake.requests[0][0] == "POST /containers/hermes/restart HTTP/1.1"
    assert fake.requests[0][2] == b""


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (403, Failure.DENIED),
        (405, Failure.DENIED),
        (200, Failure.STATUS),
        (302, Failure.STATUS),
        (404, Failure.STATUS),
        (409, Failure.STATUS),
        (500, Failure.STATUS),
    ],
)
def test_unexpected_status_is_told_without_its_message(
    fake: FakeDocker, status: int, expected: Failure
) -> None:
    fake.reply = answer(status, b"secret", "Location: http://127.0.0.1:1/")

    error = failure_of(lambda: fake.client().restart_container(5))

    assert (error.failure, error.status) == (expected, status)
    assert "secret" not in str(error)
    assert len(fake.requests) == 1


def test_nothing_listening_is_unreachable() -> None:
    with socket.create_server(("127.0.0.1", 0)) as server:
        port = server.getsockname()[1]

    client = Docker(f"http://127.0.0.1:{port}", "hermes")

    assert failure_of(lambda: client.restart_container(5)).failure is Failure.UNREACHABLE


@pytest.mark.parametrize("reply", [None, b"nonsense\r\n\r\n"])
def test_connection_closed_without_a_valid_answer_is_lost(
    fake: FakeDocker, reply: bytes | None
) -> None:
    if reply is not None:
        fake.reply = lambda client: client.sendall(reply)

    assert failure_of(lambda: fake.client().run_exec(EXEC_ID, 5)).failure is Failure.LOST


@pytest.mark.parametrize("started", [b"", b"HTTP/1.1 200 X\r\n\r\n" + frame(1, b"work\n")])
def test_silent_command_times_out(fake: FakeDocker, started: bytes) -> None:
    def silent(client: socket.socket) -> None:
        client.sendall(started)
        fake.release.wait(10)

    fake.reply = silent

    assert failure_of(lambda: fake.client().run_exec(EXEC_ID, 0.2)).failure is Failure.TIMEOUT


@pytest.mark.parametrize("head", [b"HTTP/1.1 200 X\r\n\r\n", b"HTTP/1.1 200 X\r\nA: "])
def test_answer_that_comes_drop_by_drop_cannot_last_for_ever(fake: FakeDocker, head: bytes) -> None:
    """Each reading is fast enough: only the total limit ends this."""

    def endless(client: socket.socket) -> None:
        client.sendall(head)
        body = frame(1, b"x" * 1000)
        for byte in body:
            if fake.release.wait(0.05):
                return
            client.sendall(bytes([byte]))

    fake.reply = endless

    assert failure_of(lambda: fake.client().run_exec(EXEC_ID, 0.3)).failure is Failure.TIMEOUT


@pytest.mark.parametrize(
    ("call", "status", "body"),
    [
        (lambda c: c.run_exec(EXEC_ID, 5), 200, frame(1, b"Ran now: succeeded.\n")),
        (lambda c: c.run_exec(EXEC_ID, 5), 200, b""),
        (lambda c: c.inspect_exec(EXEC_ID, 5), 200, b'{"Running": false, "ExitCode": 0}'),
        (lambda c: c.create_exec(["hermes"], "hermes", 5), 201, b'{"Id": "%s"}' % (b"a" * 64)),
    ],
)
def test_answer_shorter_than_announced_is_lost(
    fake: FakeDocker, call: Callable[[Docker], object], status: int, body: bytes
) -> None:
    fake.reply = answer(status, body, "Content-Length: 1000")

    assert failure_of(lambda: call(fake.client())).failure is Failure.LOST


def chunk(data: bytes) -> bytes:
    return f"{len(data):x}\r\n".encode() + data + b"\r\n"


@pytest.mark.parametrize("whole", [True, False])
def test_chunked_answer_counts_only_with_its_end(fake: FakeDocker, whole: bool) -> None:
    body = chunk(frame(1, b"done\n")) + (b"0\r\n\r\n" if whole else b"")
    fake.reply = answer(200, body, "Transfer-Encoding: chunked")

    if whole:
        assert fake.client().run_exec(EXEC_ID, 5).stdout == b"done\n"
    else:
        assert failure_of(lambda: fake.client().run_exec(EXEC_ID, 5)).failure is Failure.LOST


def test_negative_chunk_size_does_not_lift_the_limit(fake: FakeDocker) -> None:
    def flood(client: socket.socket) -> None:
        answer(200, b"-1\r\n", "Transfer-Encoding: chunked")(client)
        while not fake.release.is_set():
            client.sendall(b" " * 65536)

    fake.reply = flood

    assert failure_of(lambda: fake.client().inspect_exec(EXEC_ID, 5)).failure is Failure.ANSWER


def test_cut_by_the_limit_is_a_timeout_even_when_it_looks_wrong(fake: FakeDocker) -> None:
    def half(client: socket.socket) -> None:
        answer(200, b'{"Running"')(client)
        fake.release.wait(10)

    fake.reply = half

    assert failure_of(lambda: fake.client().inspect_exec(EXEC_ID, 0.2)).failure is Failure.TIMEOUT


@pytest.mark.parametrize("seconds", [0.0, -1.0])
def test_nothing_is_sent_without_time_left(fake: FakeDocker, seconds: float) -> None:
    error = failure_of(lambda: fake.client().restart_container(seconds))

    assert error.failure is Failure.UNREACHABLE
    assert fake.requests == []


def test_nothing_is_sent_when_connecting_used_the_time(
    fake: FakeDocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    times = iter([0.0, 10.0])
    monkeypatch.setattr("nurse.docker.time", SimpleNamespace(monotonic=lambda: next(times)))

    assert failure_of(lambda: fake.client().restart_container(5)).failure is Failure.UNREACHABLE
    assert fake.requests == []


@pytest.mark.parametrize("host", ["a..b", "a" * 64])
def test_name_that_cannot_be_encoded_is_unreachable(host: str) -> None:
    client = Docker(f"http://{host}:2375", "hermes")

    assert failure_of(lambda: client.restart_container(5)).failure is Failure.UNREACHABLE


@pytest.mark.parametrize("exec_id", ["../containers/other/restart", "a" * 63, "A" * 64])
def test_identifier_is_checked_before_it_enters_a_path(fake: FakeDocker, exec_id: str) -> None:
    for call in (fake.client().run_exec, fake.client().inspect_exec):
        assert failure_of(lambda: call(exec_id, 5)).failure is Failure.UNREACHABLE  # noqa: B023
    assert fake.requests == []


def test_proxy_of_the_environment_is_not_used(
    fake: FakeDocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("http_proxy", "HTTP_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:1")
    fake.reply = answer(204)

    fake.client().restart_container(5)

    assert len(fake.requests) == 1
