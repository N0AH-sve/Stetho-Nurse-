"""The protections and the startup, checked against a real server on a local port."""

import base64
import contextlib
import io
import json
import logging
import socket
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from http.client import HTTPConnection
from pathlib import Path
from typing import Any

import anyio
import pytest
import yaml
from openapi_spec_validator import validate
from starlette.responses import Response
from starlette.types import Message, Receive, Scope, Send

from stetho.__main__ import build_server, listening_socket
from stetho.config import Config, Language, Secret, parse_config
from stetho.i18n import load_translator
from stetho.logs import setup_logging
from stetho.web.app import _MAX_STOP_WAIT_SECONDS
from stetho.web.guard import Guard
from stetho.web.report import ReportStore

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "hermes-0.21"
LONG_PHRASE = "made-up pässword : long enough"
MARKER = "made-up-private-text"
EXPECTED_HEADERS = {
    "content-security-policy": (
        "default-src 'none'; style-src 'self'; script-src 'self'; connect-src 'self'; "
        "img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "x-frame-options": "DENY",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "cache-control": "no-store",
    "cross-origin-resource-policy": "same-origin",
    "cross-origin-opener-policy": "same-origin",
}


def settings(password: str = LONG_PHRASE, language: str = "en") -> dict[str, Any]:
    text = (ROOT / "config" / "stetho.example.yaml").read_text(encoding="utf-8")
    document: dict[str, Any] = yaml.safe_load(text)
    document["language"] = language
    document["auth"]["password"] = password
    document["server"] |= {"host": "127.0.0.1", "allowed_hosts": ["localhost", "[::1]"]}
    document["hermes"]["data_dir"] = str(FIXTURES / "healthy")
    return document


def config(password: str = LONG_PHRASE, language: str = "en") -> Config:
    return parse_config(settings(password, language))


@pytest.fixture
def log() -> Iterator[io.StringIO]:
    """The log as STETHO writes it."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    stream = io.StringIO()
    setup_logging(stream)
    yield stream
    root.handlers, root.level = handlers, level
    logging.captureWarnings(capture=False)


@dataclass(frozen=True)
class Answer:
    status: int
    headers: dict[str, str]
    body: bytes

    @property
    def code(self) -> str:
        return str(json.loads(self.body)["code"])


@dataclass(frozen=True)
class Client:
    port: int

    def request(
        self,
        path: str = "/health",
        *,
        method: str = "GET",
        hosts: tuple[str, ...] = ("localhost",),
        authorization: tuple[str, ...] = (),
        extra: tuple[tuple[str, str], ...] = (),
    ) -> Answer:
        connection = HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.putrequest(method, path, skip_host=True)
        for name, value in (
            *(("Host", host) for host in hosts),
            *(("Authorization", item) for item in authorization),
            *extra,
        ):
            connection.putheader(name, value.encode("latin-1"))
        connection.endheaders()
        response = connection.getresponse()
        headers = {name.lower(): value for name, value in response.getheaders()}
        answer = Answer(response.status, headers, response.read())
        connection.close()
        return answer


def basic(password: str = LONG_PHRASE, user: str = "anyone", scheme: str = "Basic") -> str:
    return f"{scheme} {base64.b64encode(f'{user}:{password}'.encode()).decode()}"


@contextlib.contextmanager
def running(settings_: Config, store: ReportStore | None = None) -> Iterator[Client]:
    server = build_server(settings_, store or ReportStore())
    ready = threading.Event()
    startup = server.startup

    async def startup_then_tell(sockets: list[socket.socket] | None = None) -> None:
        await startup(sockets=sockets)
        ready.set()

    server.startup = startup_then_tell  # type: ignore[method-assign]
    listener = listening_socket("127.0.0.1", 0)
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    assert ready.wait(10), "the server did not start"
    try:
        yield Client(listener.getsockname()[1])
    finally:
        server.should_exit = True
        thread.join(10)
        listener.close()
        assert not thread.is_alive()


@pytest.fixture
def client(log: io.StringIO) -> Iterator[Client]:
    assert not log.getvalue()
    with running(config()) as started:
        yield started


def test_health_needs_no_password(client: Client) -> None:
    answer = client.request()

    assert (answer.status, json.loads(answer.body)) == (200, {"status": "ok"})


@pytest.mark.parametrize(
    ("options", "status"),
    [
        ({"path": "/health"}, 200),
        ({"path": "/health", "hosts": ("other.example",)}, 400),
        ({"path": "/health", "method": "POST"}, 405),
        ({"path": "/nothing"}, 401),
        ({"path": "/nothing", "authenticated": True}, 404),
    ],
)
def test_every_response_has_the_security_headers(
    client: Client, options: dict[str, Any], status: int
) -> None:
    authorization = (basic(),) if options.pop("authenticated", False) else ()

    answer = client.request(**options, authorization=authorization)

    assert answer.status == status
    assert {name: answer.headers.get(name) for name in EXPECTED_HEADERS} == EXPECTED_HEADERS
    assert "server" not in answer.headers
    assert not any(name.startswith("access-control-") for name in answer.headers)


@pytest.mark.parametrize("host", ["localhost", "LOCALHOST", "localhost:8090", "[::1]", "[::1]:1"])
def test_allowed_host_is_accepted_with_any_port(client: Client, host: str) -> None:
    assert client.request(hosts=(host,)).status == 200


@pytest.mark.parametrize(
    "hosts",
    [
        (),
        ("",),
        ("evil.example",),
        ("localhost.evil.example",),
        ("localhost.",),
        ("localhost:abc",),
        ("localhost:123456",),
        ("localhost@evil.example",),
        ("localhost, evil.example",),
        ("::1",),
        ("[::2]",),
        ("localhost", "localhost"),
        ("localhost", "evil.example"),
    ],
)
def test_other_host_is_refused_everywhere(client: Client, hosts: tuple[str, ...]) -> None:
    for path in ("/health", "/api/v1/status"):
        answer = client.request(path, hosts=hosts, authorization=(basic(),))

        assert answer.status == 400


def test_forwarded_headers_do_not_change_the_host(client: Client) -> None:
    forwarded = (("X-Forwarded-Host", "localhost"), ("Forwarded", "host=localhost"))

    assert client.request(hosts=("evil.example",), extra=forwarded).status == 400


@pytest.mark.parametrize("path", ["/", "/api/v1/status", "/static/x.js", "/health/", "/Health"])
def test_everything_else_needs_the_password(client: Client, path: str) -> None:
    answer = client.request(path)

    assert (answer.status, answer.code) == (401, "unauthorized")
    assert answer.headers["www-authenticate"] == 'Basic realm="STETHO", charset="UTF-8"'


@pytest.mark.parametrize("user", ["anyone", "", "admin"])
def test_user_name_is_ignored(client: Client, user: str) -> None:
    assert client.request("/x", authorization=(basic(user=user),)).status == 404


def test_scheme_name_may_have_any_case(client: Client) -> None:
    assert client.request("/x", authorization=(basic(scheme="BASIC"),)).status == 404


@pytest.mark.parametrize(
    "authorization",
    [
        (basic("wrong password, long enough"),),
        (basic(LONG_PHRASE + "x"),),
        (basic(LONG_PHRASE[:-1]),),
        (basic(""),),
        (f"Bearer {MARKER}",),
        ("Basic",),
        ("Basic ***",),
        (f"Basic {base64.b64encode(b'no colon').decode()}",),
        (f"Basic {base64.b64encode(bytes([255, 58, 255])).decode()}",),
        (basic(), basic()),
    ],
)
def test_wrong_or_malformed_credentials_are_refused_and_logged(
    client: Client, log: io.StringIO, authorization: tuple[str, ...]
) -> None:
    answer = client.request(f"/{MARKER}", authorization=authorization)

    assert answer.status == 401
    lines = [line for line in log.getvalue().splitlines() if "authentication" in line]
    assert len(lines) == 1
    assert lines[0].endswith("WARNING stetho.web.guard: authentication failed")
    for hidden in (LONG_PHRASE, MARKER, "anyone", authorization[0].split()[-1]):
        if hidden != "Basic":
            assert hidden not in log.getvalue()


def test_visit_without_credentials_is_not_logged(client: Client, log: io.StringIO) -> None:
    client.request("/x")

    assert "authentication" not in log.getvalue()


def test_accesses_are_not_logged(client: Client, log: io.StringIO) -> None:
    client.request(f"/{MARKER}?q={MARKER}", authorization=(basic(),))
    client.request()

    assert MARKER not in log.getvalue()
    assert "/health" not in log.getvalue()


@pytest.mark.usefixtures("log")
def test_without_a_password_the_proxy_is_trusted() -> None:
    with running(config(password="")) as client:
        assert client.request("/x").status == 404
        assert client.request("/x", hosts=("evil.example",)).status == 400


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "X"])
def test_only_reading_methods_are_accepted(client: Client, method: str) -> None:
    for path in ("/health", "/api/v1/status"):
        answer = client.request(path, method=method, authorization=(basic(),))

        assert (answer.status, answer.headers["allow"]) == (405, "GET, HEAD")


def test_password_is_checked_before_the_method(client: Client) -> None:
    assert client.request("/x", method="POST").status == 401


def test_head_gets_no_body(client: Client) -> None:
    answer = client.request(method="HEAD")

    assert (answer.status, answer.body) == (200, b"")


def test_trailing_slash_does_not_redirect(client: Client) -> None:
    answer = client.request("/health/", authorization=(basic(),))

    assert answer.status == 404
    assert "location" not in answer.headers


@pytest.mark.usefixtures("log")
@pytest.mark.parametrize(
    ("language", "message"),
    [("en", "A password is required."), ("fr", "Un mot de passe est demandé.")],
)
def test_error_messages_follow_the_installation_language(language: str, message: str) -> None:
    with running(config(language=language)) as client:
        assert json.loads(client.request("/x").body) == {
            "code": "unauthorized",
            "message": message,
        }


def test_errors_follow_the_contract(client: Client) -> None:
    contract = yaml.safe_load((ROOT / "stetho" / "openapi.yaml").read_text(encoding="utf-8"))
    validate(contract)
    codes = set(contract["components"]["schemas"]["Error"]["properties"]["code"]["enum"])
    answers = [
        client.request(hosts=("x",)),
        client.request("/x"),
        client.request("/x", authorization=(basic(),)),
        client.request(method="POST"),
    ]

    for answer in answers:
        assert set(json.loads(answer.body)) == {"code", "message"}
        assert answer.code in codes


def test_websocket_is_refused(client: Client) -> None:
    nonce = base64.b64encode(bytes(16)).decode()  # any 16 bytes make a valid handshake
    handshake = (
        "GET /health HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n"
        "Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\n"
        f"Sec-WebSocket-Key: {nonce}\r\n\r\n"
    )
    with socket.create_connection(("127.0.0.1", client.port), timeout=5) as connection:
        connection.sendall(handshake.encode())
        first_line = connection.recv(1024).split(b"\r\n")[0]

    assert b"101" not in first_line  # never upgraded: treated as a plain request


def call_guard(scope: Scope, application: Any) -> list[Message]:
    sent: list[Message] = []
    guard = Guard(
        application,
        allowed_hosts=("localhost",),
        password=Secret(""),
        translator=load_translator(Language.EN),
    )

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    anyio.run(guard, scope, receive, send)
    return sent


HTTP_SCOPE: Scope = {
    "type": "http",
    "method": "GET",
    "path": "/x",
    "headers": [(b"host", b"localhost")],
}


async def never(_scope: Scope, _receive: Receive, _send: Send) -> None:
    pytest.fail("the application must not be reached")


@pytest.mark.parametrize("hosts", [[], [b"localhost", b"localhost"], [b"localhost", b"x"]])
def test_guard_itself_refuses_a_missing_or_repeated_host(hosts: list[bytes]) -> None:
    # The web server already refuses these: the guard must not rely on it.
    scope = {**HTTP_SCOPE, "headers": [(b"host", host) for host in hosts]}

    sent = call_guard(scope, never)

    assert sent[0]["status"] == 400
    assert json.loads(sent[1]["body"])["code"] == "invalid_host"


def test_other_protocols_never_reach_the_application() -> None:
    assert call_guard({"type": "websocket"}, never) == [{"type": "websocket.close", "code": 1008}]
    assert call_guard({"type": "unknown"}, never) == []


def test_protective_headers_cannot_be_weakened_by_a_route() -> None:
    async def careless(scope: Scope, receive: Receive, send: Send) -> None:
        headers = {"Cache-Control": "public", "Access-Control-Allow-Origin": "*"}
        await Response("x", headers=headers)(scope, receive, send)

    start = call_guard(HTTP_SCOPE, careless)[0]

    names = [name for name, _ in start["headers"]]
    assert names.count(b"cache-control") == 1
    assert (b"cache-control", b"no-store") in start["headers"]
    assert b"access-control-allow-origin" not in names


def test_error_outside_the_routes_shows_nothing(caplog: pytest.LogCaptureFixture) -> None:
    async def broken(_scope: Scope, _receive: Receive, _send: Send) -> None:
        raise KeyError(MARKER)

    sent = call_guard(HTTP_SCOPE, broken)

    assert sent[0]["status"] == 500
    assert json.loads(sent[1]["body"])["code"] == "internal_error"
    assert MARKER not in repr(sent) + caplog.text
    assert "request failed: KeyError" in caplog.text


def test_error_after_the_response_started_sends_nothing_more(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def half(_scope: Scope, _receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        raise ValueError(MARKER)

    sent = call_guard(HTTP_SCOPE, half)

    assert [message["type"] for message in sent] == ["http.response.start"]
    assert MARKER not in caplog.text


def test_error_in_a_route_shows_nothing_anywhere(
    log: io.StringIO, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    def failing(*_arguments: object, **_options: object) -> None:
        raise RuntimeError(MARKER)

    # Any route that fails: here, the one that builds the status.
    monkeypatch.setattr("stetho.web.routes.status_json", failing)

    with running(config()) as client:
        answer = client.request("/api/v1/status", authorization=(basic(),))

    assert (answer.status, answer.code) == (500, "internal_error")
    assert answer.headers["x-frame-options"] == "DENY"
    printed = capfd.readouterr()
    assert MARKER not in answer.body.decode() + log.getvalue() + printed.out + printed.err
    assert "RuntimeError" in log.getvalue()


def test_rhythm_of_the_settings_is_written_at_startup(log: io.StringIO) -> None:
    document = settings()
    document["probe"] = {"interval_seconds": 45, "sensor_timeout_seconds": 7}

    with running(parse_config(document)):
        pass

    assert "STETHO has started: a reading every 45 s, at most 7 s for each sensor" in log.getvalue()


@pytest.mark.usefixtures("log")
def test_stuck_probe_does_not_hold_the_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    release = threading.Event()
    monkeypatch.setattr("stetho.web.app._MAX_STOP_WAIT_SECONDS", 0.2)
    # A probe that never looks at the request to stop, as while it waits for a stuck sensor.
    monkeypatch.setattr("stetho.probe.Probe.run_forever", lambda *_arguments: release.wait(60))
    document = settings()
    document["probe"]["sensor_timeout_seconds"] = 86400

    try:
        with running(parse_config(document)):  # fails if the stop takes more than 10 s
            pass
    finally:
        release.set()


def test_stop_waits_at_most_five_seconds_for_the_probe() -> None:
    assert _MAX_STOP_WAIT_SECONDS == 5


@pytest.mark.usefixtures("log")
def test_probe_runs_while_the_server_does() -> None:
    published = threading.Event()

    class Watched(ReportStore):
        def publish(self, report: Any) -> None:
            super().publish(report)
            published.set()

    store = Watched()

    with running(config(), store):
        assert published.wait(5)
        assert "probe" in {thread.name for thread in threading.enumerate()}

    assert "probe" not in {thread.name for thread in threading.enumerate()}
    assert store.latest is not None
    assert [result.id for result in store.latest.sensors] == ["gateway"]
    assert not store.failed


def test_password_is_compared_in_constant_time(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    compared: list[tuple[int, int]] = []

    def spy(first: bytes, second: bytes) -> bool:
        compared.append((len(first), len(second)))
        return first == second

    monkeypatch.setattr("secrets.compare_digest", spy)

    client.request("/x", authorization=(basic("short"),))

    assert compared == [(32, 32)]  # digests: the length of the password is not compared


@pytest.mark.parametrize("sent", ["[::1]", "[0:0:0:0:0:0:0:1]", "[0000::0001]:80"])
def test_every_written_form_of_an_ipv6_address_is_the_same(sent: str) -> None:
    async def reached(scope: Scope, receive: Receive, send: Send) -> None:
        await Response("x")(scope, receive, send)

    guard_scope = {**HTTP_SCOPE, "headers": [(b"host", sent.encode())]}
    messages: list[Message] = []
    guard = Guard(
        reached,
        allowed_hosts=("[0:0:0:0:0:0:0:1]",),
        password=Secret(""),
        translator=load_translator(Language.EN),
    )

    async def receive() -> Message:
        return {"type": "http.request"}

    async def send(message: Message) -> None:
        messages.append(message)

    anyio.run(guard, guard_scope, receive, send)

    assert messages[0]["status"] == 200
