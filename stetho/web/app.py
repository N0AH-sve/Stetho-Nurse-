"""The application: its routes, the probe that runs beside them and the protections."""

import contextlib
import logging
import threading
from collections.abc import AsyncIterator

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp

from stetho.config import Config
from stetho.errors import summarise_error
from stetho.i18n import Translator
from stetho.probe import Probe, ProbeReport
from stetho.web.guard import HEALTH_PATH, Guard, error_response

_LOG = logging.getLogger(__name__)


class ReportStore:
    """The latest report of the probe. Written by its thread, read by the requests."""

    def __init__(self) -> None:
        self.latest: ProbeReport | None = None
        self.failed = False  # the startup or the shutdown of the application failed

    def publish(self, report: ProbeReport) -> None:
        self.latest = report  # replaced as a whole: a reader never sees half a report


async def _health(_request: Request) -> Response:
    return JSONResponse({"status": "ok"})


def create_app(config: Config, translator: Translator, probe: Probe, store: ReportStore) -> ASGIApp:
    """The protected application. The probe runs from startup to shutdown."""

    @contextlib.asynccontextmanager
    async def probing(_app: Starlette) -> AsyncIterator[None]:
        stop = threading.Event()
        thread = threading.Thread(
            target=probe.run_forever,
            args=(config.probe.interval_seconds, stop, store.publish),
            name="probe",
            daemon=True,
        )
        thread.start()
        _LOG.info("STETHO has started")
        try:
            yield
        finally:
            stop.set()
            await run_in_threadpool(thread.join, config.probe.sensor_timeout_seconds + 1)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        try:
            async with probing(app):
                yield
        except Exception as error:
            # Written here: the web server only passes on a text, which is not shown.
            store.failed = True
            summary = summarise_error(error)
            calls = "".join(f"\n  {call}" for call in summary.calls)
            _LOG.error("startup or shutdown failed: %s%s", summary.error_type, calls)
            raise

    async def http_error(_request: Request, error: Exception) -> Response:
        assert isinstance(error, HTTPException)  # noqa: S101 - registered for it only
        code = "not_found" if error.status_code == 404 else "internal_error"  # noqa: PLR2004
        return error_response(translator, error.status_code, code)

    async def unexpected_error(_request: Request, _error: Exception) -> Response:
        return error_response(translator, 500, "internal_error")

    app = Starlette(
        routes=[Route(HEALTH_PATH, _health)],
        exception_handlers={HTTPException: http_error, Exception: unexpected_error},
        lifespan=lifespan,
    )
    # A redirection would be an address built from the request.
    app.router.redirect_slashes = False
    return Guard(
        app,
        allowed_hosts=config.server.allowed_hosts,
        password=config.auth.password,
        translator=translator,
    )
