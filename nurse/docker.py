"""The only calls NURSE makes to Docker, through the filtering proxy.

Four calls, all on the configured container: prepare a command, run it, read how it ended and
restart the container. Each call has a total time limit and keeps a limited answer. No error
carries a message: it could hold anything that Docker or the command wrote.
"""

import http.client
import json
import re
import socket
import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlsplit

# The end of an output is kept: this is where a command says how it ended.
OUTPUT_BYTES = 64 * 1024
_MAX_ANSWER_BYTES = 64 * 1024  # a JSON answer
_CHUNK_BYTES = 16 * 1024
_EXEC_ID = re.compile(r"[0-9a-f]{64}")
# The header of a frame: the stream, three zeros and the size of what follows.
_HEADER_BYTES = 8
_STDOUT = 1
_STDERR = 2


class Failure(Enum):
    """Only `UNREACHABLE` and `DENIED` say that nothing happened. After any other failure of a
    command or a restart, its outcome is unknown: it may have run, and may go on."""

    UNREACHABLE = "unreachable"  # nothing was sent
    DENIED = "denied"  # the proxy does not allow the call
    STATUS = "status"  # another unexpected status
    TIMEOUT = "timeout"  # sent, and not finished in time
    LOST = "lost"  # sent, and the connection broke or the answer was cut
    ANSWER = "answer"  # an answer that cannot be understood


class DockerError(Exception):
    def __init__(self, failure: Failure, status: int | None = None) -> None:
        super().__init__(failure.value)
        self.failure = failure
        self.status = status


@dataclass(frozen=True)
class Output:
    """What a command wrote, each stream apart so that no line is cut by the other."""

    stdout: bytes
    stderr: bytes
    truncated: bool  # only the last whole lines of a stream were kept
    # The answer did not stop in the middle of a frame. It may still have been cut between two:
    # only the state of the command and the proof of its effect say how it ended.
    complete: bool


@dataclass(frozen=True)
class ExecState:
    running: bool
    exit_code: int | None


def _check_whole(response: http.client.HTTPResponse) -> None:
    """`read1` says nothing when an answer ends before the length that it announced."""
    if response.length:
        raise DockerError(Failure.LOST)


def _read_output(response: http.client.HTTPResponse) -> Output:
    """Separates the two streams of a command without ever holding more than their ends."""
    kept = {_STDOUT: bytearray(), _STDERR: bytearray()}
    shortened: set[int] = set()
    header = bytearray()
    stream, remaining = _STDOUT, 0
    while chunk := response.read1(_CHUNK_BYTES):
        position = 0
        while position < len(chunk):
            if remaining:
                part = chunk[position : position + remaining]
                position += len(part)
                remaining -= len(part)
                tail = kept[stream]
                tail += part
                if len(tail) > OUTPUT_BYTES:
                    del tail[:-OUTPUT_BYTES]
                    shortened.add(stream)
                continue
            part = chunk[position : position + _HEADER_BYTES - len(header)]
            position += len(part)
            header += part
            if len(header) == _HEADER_BYTES:
                stream, remaining = header[0], int.from_bytes(header[4:], "big")
                if stream not in kept or any(header[1:4]):
                    raise DockerError(Failure.ANSWER)
                header.clear()
    _check_whole(response)
    for stream in shortened:
        # The end of a long line, such as a name, must not pass for a whole line.
        tail = kept[stream]
        end = tail.find(b"\n")
        del tail[: len(tail) if end < 0 else end + 1]
    complete = not header and not remaining
    return Output(bytes(kept[_STDOUT]), bytes(kept[_STDERR]), bool(shortened), complete)


def _read_object(response: http.client.HTTPResponse) -> dict[str, object]:
    # Not `read`: with a negative chunk size it would read without any limit.
    content = bytearray()
    while chunk := response.read1(_CHUNK_BYTES):
        content += chunk
        if len(content) > _MAX_ANSWER_BYTES:
            raise DockerError(Failure.ANSWER)
    _check_whole(response)
    try:
        document = json.loads(content)
    except (ValueError, RecursionError):
        raise DockerError(Failure.ANSWER) from None
    if not isinstance(document, dict):
        raise DockerError(Failure.ANSWER)
    return document


def _check_exec_id(exec_id: str) -> None:
    """The identifier becomes part of a path."""
    if not _EXEC_ID.fullmatch(exec_id):
        raise DockerError(Failure.UNREACHABLE)


def _check_status(status: int, expected: int) -> None:
    if status in (403, 405):
        raise DockerError(Failure.DENIED, status)
    if status != expected:
        raise DockerError(Failure.STATUS, status)


class Docker:
    def __init__(self, proxy_url: str, container: str) -> None:
        """Both come from the settings, which check them: the container is part of a path."""
        parts = urlsplit(proxy_url)
        self._host, self._port = str(parts.hostname), parts.port
        self._container = container

    def create_exec(self, argv: Sequence[str], user: str, seconds: float) -> str:
        """Prepares a command and returns its identifier. Nothing runs yet."""
        body = {
            "AttachStdin": False,
            "AttachStdout": True,
            "AttachStderr": True,
            "Tty": False,
            "Privileged": False,
            "User": user,
            "Cmd": list(argv),
        }
        path = f"/containers/{self._container}/exec"
        with self._exchange("POST", path, body, 201, seconds) as response:
            exec_id = _read_object(response).get("Id")
        if not isinstance(exec_id, str) or not _EXEC_ID.fullmatch(exec_id):
            raise DockerError(Failure.ANSWER)  # it would become part of a path
        return exec_id

    def run_exec(self, exec_id: str, seconds: float) -> Output:
        """Runs a prepared command and waits for the end of its output.

        After a failure other than `UNREACHABLE` or `DENIED` the command may go on: Docker has
        no call to stop it.
        """
        _check_exec_id(exec_id)
        body = {"Detach": False, "Tty": False}
        with self._exchange("POST", f"/exec/{exec_id}/start", body, 200, seconds) as response:
            return _read_output(response)

    def inspect_exec(self, exec_id: str, seconds: float) -> ExecState:
        _check_exec_id(exec_id)
        with self._exchange("GET", f"/exec/{exec_id}/json", None, 200, seconds) as response:
            document = _read_object(response)
        running, code = document.get("Running"), document.get("ExitCode")
        if not isinstance(running, bool) or not (code is None or type(code) is int):
            raise DockerError(Failure.ANSWER)
        return ExecState(running, None if running else code)

    def restart_container(self, seconds: float) -> None:
        """Returns once Docker has restarted it. This says nothing about the health of Hermes."""
        path = f"/containers/{self._container}/restart"
        with self._exchange("POST", path, None, 204, seconds):
            pass

    @contextmanager
    def _exchange(
        self, method: str, path: str, body: object, expected: int, seconds: float
    ) -> Iterator[http.client.HTTPResponse]:
        """One request on its own connection, cut `seconds` after the start of the call.

        The limit does not cover the resolution of the name, and each of its addresses may take
        `seconds` to connect. Nothing is sent once the time is over.

        Neither a redirection nor a proxy of the environment is followed.
        """
        if seconds <= 0:
            raise DockerError(Failure.UNREACHABLE)
        deadline = time.monotonic() + seconds
        connection = http.client.HTTPConnection(self._host, self._port, timeout=seconds)
        try:
            try:
                connection.connect()
            except (OSError, UnicodeError):  # a name that cannot be encoded is a `UnicodeError`
                raise DockerError(Failure.UNREACHABLE) from None
            left = deadline - time.monotonic()
            if left <= 0:
                raise DockerError(Failure.UNREACHABLE)
            expired = threading.Event()
            opened = connection.sock

            def cut() -> None:
                expired.set()
                with suppress(OSError):
                    opened.shutdown(socket.SHUT_RDWR)

            # A limit on each reading would let an answer that comes drop by drop last for ever.
            timer = threading.Timer(left, cut)
            timer.daemon = True
            timer.start()
            try:
                content = None if body is None else json.dumps(body).encode("ascii")
                headers = {"Connection": "close"}
                if content is not None:
                    headers["Content-Type"] = "application/json"
                connection.request(method, path, content, headers)
                # Closed here: on its own connection, the response is what holds it.
                with connection.getresponse() as response:
                    _check_status(response.status, expected)
                    yield response
            except (OSError, http.client.HTTPException) as error:
                late = expired.is_set() or isinstance(error, TimeoutError)
                raise DockerError(Failure.TIMEOUT if late else Failure.LOST) from None
            except DockerError:
                if expired.is_set():  # a cut answer looks like a wrong one
                    raise DockerError(Failure.TIMEOUT) from None
                raise
            else:
                if expired.is_set():  # a cut answer can also look like a complete one
                    raise DockerError(Failure.TIMEOUT)
            finally:
                timer.cancel()
        finally:
            connection.close()
