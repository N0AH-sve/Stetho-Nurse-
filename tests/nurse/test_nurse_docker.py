import json
import socket
import threading
from collections.abc import Callable, Iterator
from contextlib import suppress

import pytest

from nurse.docker import OUTPUT_BYTES, Docker, DockerError, ExecState, Failure, Output

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
            received += client.recv(4096)
        head, _, body = received.partition(b"\r\n\r\n")
        first, *lines = head.decode().split("\r\n")
        headers = dict(line.split(": ", 1) for line in lines)
        while len(body) < int(headers.get("Content-Length", 0)):
            body += client.recv(4096)
        self.requests.append((first, headers, body))

    def close(self) -> None:
        self.release.set()
        self._server.close()

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


@pytest.mark.parametrize("body", [b"{", b"[" * 100_000, b" " * (64 * 1024 + 1)])
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


def test_only_the_end_of_a_long_output_is_kept(fake: FakeDocker) -> None:
    fake.reply = answer(200, frame(1, b"x" * (3 * OUTPUT_BYTES)) + frame(1, b"the end\n"))

    output = fake.client().run_exec(EXEC_ID, 5)

    assert output.truncated
    assert len(output.stdout) == OUTPUT_BYTES
    assert output.stdout.endswith(b"xxxthe end\n")


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
