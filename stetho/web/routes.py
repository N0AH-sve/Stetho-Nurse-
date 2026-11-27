"""The reading routes of the API. They read the latest report: no request runs a sensor."""

from collections.abc import Awaitable, Callable
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from stetho.anomalies import AnomalyCode
from stetho.config import Config
from stetho.i18n import Translator
from stetho.patterns import SENSOR_ID
from stetho.web.guard import HEALTH_PATH, error_response
from stetho.web.report import (
    ReportStore,
    View,
    anomaly_json,
    sensor_json,
    stale_after_seconds,
    status_json,
    view,
)
from stetho.yamlfile import load_yaml

CONTRACT_FILE = Path(__file__).parents[1] / "openapi.yaml"
TEMPLATES_DIR = Path(__file__).parent / "templates"

type Handler = Callable[[Request], Awaitable[Response]]


def _operations(contract: dict[str, object]) -> list[dict[str, object]]:
    paths = contract["paths"]
    assert isinstance(paths, dict)  # noqa: S101 - our own file, checked by the tests
    return [
        {
            "method": method.upper(),
            "path": path,
            "summary": operation["summary"],
            "description": operation.get("description", ""),
            "served": operation.get("x-stetho-served", True),
        }
        for path, item in paths.items()
        for method, operation in item.items()
    ]


def build_routes(config: Config, translator: Translator, store: ReportStore) -> list[Route]:
    """Everything that can fail is loaded here, before the server starts."""
    contract = load_yaml(CONTRACT_FILE)
    if not isinstance(contract, dict):
        raise TypeError("the API contract must be a mapping")
    environment = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR), autoescape=True, undefined=StrictUndefined
    )
    documentation = environment.get_template("api_docs.html").render(
        language=config.language.value,
        title=translator.text("api.docs.title"),
        intro=translator.text("api.docs.intro"),
        not_served=translator.text("api.docs.not_served"),
        operations=_operations(contract),
    )
    stale_after = stale_after_seconds(config.probe)

    def current() -> View:
        report, stale = store.read(stale_after)
        return view(report, translator, stale=stale)

    def strict(handler: Handler, *parameters: str) -> Handler:
        """Refuse unknown or repeated query parameters, which could be silently ignored."""

        async def checked(request: Request) -> Response:
            names = [name for name, _ in request.query_params.multi_items()]
            if len(set(names)) != len(names) or set(names) - set(parameters):
                return error_response(translator, 400, "invalid_request")
            return await handler(request)

        return checked

    async def health(_request: Request) -> Response:
        return JSONResponse({"status": "ok"})

    async def status(_request: Request) -> Response:
        return JSONResponse(status_json(current()))

    async def sensor(request: Request) -> Response:
        identifier = request.path_params["sensor_id"]
        now = current()
        if SENSOR_ID.fullmatch(identifier):
            for result in now.sensors:
                if result.id == identifier:
                    return JSONResponse(sensor_json(result, stale=now.stale))
        return error_response(translator, 404, "not_found")

    async def anomalies(request: Request) -> Response:
        module = request.query_params.get("module")
        code = request.query_params.get("code")
        if (module is not None and not SENSOR_ID.fullmatch(module)) or (
            code is not None and code not in {item.value for item in AnomalyCode}
        ):
            return error_response(translator, 400, "invalid_request")
        now = current()
        checked_at = now.checked_at
        return JSONResponse(
            {
                "checked_at": None if checked_at is None else checked_at.isoformat(),
                "stale": now.stale,
                "anomalies": [
                    anomaly_json(entry, translator)
                    for entry in now.anomalies
                    if module in (None, entry.anomaly.module)
                    and code in (None, entry.anomaly.code.value)
                ],
            }
        )

    async def openapi(_request: Request) -> Response:
        return JSONResponse(contract)

    async def docs(_request: Request) -> Response:
        return HTMLResponse(documentation)

    return [
        Route(HEALTH_PATH, strict(health)),
        Route("/api/v1/status", strict(status)),
        Route("/api/v1/sensors/{sensor_id}", strict(sensor)),
        Route("/api/v1/anomalies", strict(anomalies, "module", "code")),
        Route("/api/v1/openapi.json", strict(openapi)),
        Route("/api/docs", strict(docs)),
    ]
