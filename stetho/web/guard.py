"""The protections of every request, around the whole application.

In this order: the protocol, the host name, the password, the method. Every response gets the
security headers. An unexpected error shows nothing and is logged without its message.
"""

import base64
import binascii
import hashlib
import logging
import re
import secrets

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from stetho.config import Secret
from stetho.errors import summarise_error
from stetho.i18n import Translator

_LOG = logging.getLogger(__name__)
HEALTH_PATH = "/health"
_METHODS = ("GET", "HEAD")
_CHALLENGE = 'Basic realm="STETHO", charset="UTF-8"'
# `[IPv6]` or a name or IPv4 address, with an optional port.
_HOST = re.compile(r"(\[[0-9A-Fa-f:.]+\]|[^\[\]:]+)(:[0-9]{1,5})?")
_SECURITY_HEADERS = (
    (
        b"content-security-policy",
        b"default-src 'none'; style-src 'self'; script-src 'self'; connect-src 'self'; "
        b"img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    ),
    (b"x-frame-options", b"DENY"),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"cache-control", b"no-store"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"cross-origin-opener-policy", b"same-origin"),
)
_SECURITY_NAMES = frozenset(name for name, _ in _SECURITY_HEADERS)
_WEBSOCKET_REFUSED = 1008  # policy violation


def error_response(
    translator: Translator, status: int, code: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    """An error in the format of the API contract."""
    return JSONResponse(
        {"code": code, "message": translator.text(f"api.error.{code}")},
        status_code=status,
        headers=headers,
    )


def _digest(value: bytes) -> bytes:
    # Compared digests have the same length, so the comparison does not tell the length.
    return hashlib.sha256(value).digest()


def _header(scope: Scope, name: bytes) -> list[bytes]:
    return [value for key, value in scope["headers"] if key.lower() == name]


def _password_of(authorization: bytes) -> bytes | None:
    """The password of a Basic header, or None if the header is malformed."""
    scheme, _, encoded = authorization.partition(b" ")
    if scheme.lower() != b"basic":
        return None
    try:
        decoded = base64.b64decode(encoded.strip(), validate=True)
        decoded.decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    _user, colon, password = decoded.partition(b":")
    return password if colon else None


class Guard:
    def __init__(
        self,
        app: ASGIApp,
        *,
        allowed_hosts: tuple[str, ...],
        password: Secret,
        translator: Translator,
    ) -> None:
        self._app = app
        self._hosts = frozenset(host.lower() for host in allowed_hosts)
        self._password = _digest(password.reveal().encode()) if password else None
        self._translator = translator

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self._app(scope, receive, send)
            return
        if scope["type"] != "http":
            # A WebSocket would skip checks written for HTTP: nothing else is accepted.
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": _WEBSOCKET_REFUSED})
            return
        await self._http(scope, receive, send)

    async def _http(self, scope: Scope, receive: Receive, send: Send) -> None:
        started = False

        async def send_protected(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() not in _SECURITY_NAMES
                    and not name.lower().startswith(b"access-control-")
                ]
                message = {**message, "headers": [*headers, *_SECURITY_HEADERS]}
            await send(message)

        try:
            refusal = self._refusal(scope)
            await (refusal or self._app)(scope, receive, send_protected)
        except Exception as error:  # noqa: BLE001 - nothing of an error may leave
            summary = summarise_error(error)
            calls = "".join(f"\n  {call}" for call in summary.calls)
            _LOG.error("request failed: %s%s", summary.error_type, calls)
            if not started:
                response = error_response(self._translator, 500, "internal_error")
                await response(scope, receive, send_protected)

    def _refusal(self, scope: Scope) -> JSONResponse | None:
        if not self._host_allowed(scope):
            return error_response(self._translator, 400, "invalid_host")
        if scope["path"] != HEALTH_PATH and not self._authenticated(scope):
            return error_response(
                self._translator, 401, "unauthorized", {"WWW-Authenticate": _CHALLENGE}
            )
        if scope["method"] not in _METHODS:
            return error_response(
                self._translator, 405, "method_not_allowed", {"Allow": ", ".join(_METHODS)}
            )
        return None

    def _host_allowed(self, scope: Scope) -> bool:
        values = _header(scope, b"host")
        if len(values) != 1:
            return False
        match = _HOST.fullmatch(values[0].decode("latin-1"))
        return match is not None and match[1].lower() in self._hosts

    def _authenticated(self, scope: Scope) -> bool:
        if self._password is None:
            return True  # the reverse proxy authenticates
        values = _header(scope, b"authorization")
        if not values:
            return False  # the first visit of a browser: not a failed attempt
        password = _password_of(values[0]) if len(values) == 1 else None
        if password is not None and secrets.compare_digest(_digest(password), self._password):
            return True
        # Nothing about the attempt is written: it could hold a password.
        _LOG.warning("authentication failed")
        return False
