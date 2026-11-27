"""The status page as the server renders it. Its script is only checked by reading it."""

import re
from pathlib import Path

import pytest
from test_api import FIXTURES, NOW, Api, FakeSensor, config, full_report, result, run_probe

from stetho.anomalies import Anomaly, AnomalyCode
from stetho.config import Language
from stetho.i18n import load_translator
from stetho.probe import ProbeReport
from stetho.results import Detail, Item, Status
from stetho.web.page import build_page
from stetho.web.report import view

STATIC = Path(__file__).parents[2] / "stetho" / "web" / "static"
HOSTILE = '<img src=x onerror="alert(1)">'


def page_of(api: Api) -> str:
    answer = api.get("/")
    assert answer.status == 200
    assert answer.content_type.startswith("text/html")
    return answer.body.decode()


def published(report: ProbeReport, language: str = "en") -> Api:
    api = Api(config(language=language))
    api.store.publish(report)
    return api


@pytest.mark.parametrize(
    ("language", "title", "nothing"),
    [
        ("en", "Hermes status", "Nothing could be checked"),
        ("fr", "État de Hermes", "Rien n&#39;a pu être vérifié"),
        ("es", "Estado de Hermes", "No se ha podido comprobar nada"),
    ],
)
def test_before_the_first_pass_nothing_looks_healthy(
    language: str, title: str, nothing: str
) -> None:
    page = page_of(Api(config(language=language)))

    assert f'<html lang="{language}">' in page
    assert f"<title>{title}</title>" in page
    assert f"<h1>{nothing}</h1>" in page
    assert 'class="banner status-unknown"' in page
    assert "status-ok" not in page


@pytest.mark.parametrize(
    ("scenario", "status"),
    [
        ("healthy", "ok"),
        ("degraded", "warn"),
        ("stopped", "critical"),
        ("invalid/truncated-json", "unknown"),
    ],
)
def test_banner_takes_the_overall_status(scenario: str, status: str) -> None:
    api = Api(config(FIXTURES / scenario))
    api.store.publish(run_probe(api.config))

    assert f'class="banner status-{status}"' in page_of(api)


def test_healthy_page_shows_no_problem_nor_anomaly() -> None:
    api = Api()
    api.store.publish(run_probe(api.config))

    page = page_of(api)

    assert "Everything is working" in page
    assert 'id="problems"' not in page
    assert 'data-key="anomalies"' not in page
    assert 'data-key="sensor-gateway"' in page


def test_reading_time_is_relative_with_the_exact_date_on_hover() -> None:
    api = published(full_report())

    page = page_of(api)

    assert 'title="2026-01-15 13:00:00 (Europe/Paris)"' in page
    assert 'datetime="2026-01-15T12:00:00+00:00"' in page


def test_anomalies_are_counted_and_listed() -> None:
    report = full_report()

    page = page_of(published(report))

    assert f"Anomalies ({len(report.anomalies)})" in page
    for code in AnomalyCode:
        assert f"<code>{code.value}</code>" in page
    assert "hidden value" in page


def test_stale_page_offers_no_advice_and_looks_unknown() -> None:
    api = published(full_report())
    assert "Do this" in page_of(api)
    api.time.seconds += 1000

    page = page_of(api)

    assert 'class="banner status-unknown"' in page
    assert "The data is out of date" in page
    for gone in ("Do this", "Look", "Last error", "status-ok", "status-critical"):
        assert gone not in page


def test_everything_from_outside_is_escaped() -> None:
    item = Item(
        id="x",
        label=HOSTILE,
        status=Status.WARN,
        summary=HOSTILE,
        advice=HOSTILE,
        details=(Detail(label=HOSTILE, value=HOSTILE),),
    )
    hostile = result(
        "one",
        Status.WARN,
        title=HOSTILE,
        group=HOSTILE,
        summary=HOSTILE,
        advice=HOSTILE,
        items=(item,),
        details=(Detail(label=HOSTILE, value=HOSTILE),),
    )
    anomaly = Anomaly(code=AnomalyCode.SCHEMA_CHANGED, module="one", source=HOSTILE, field=HOSTILE)

    page = page_of(published(run_probe(config(), [FakeSensor("one", hostile, anomaly)])))

    assert "<img" not in page
    assert 'onerror="' not in page
    assert "&lt;img src=x" in page


def problems(*sensors: object) -> list[tuple[str, str]]:
    report = run_probe(config(), list(sensors))  # type: ignore[arg-type]
    translator = load_translator(Language.EN)
    page = build_page(view(report, translator, stale=False), NOW, translator, config().timezone)
    return [(problem.status.value, problem.source) for problem in page.problems]


def item(identifier: str, status: Status) -> Item:
    return Item(id=identifier, label=identifier, status=status, summary="s")


def test_problems_are_listed_from_the_most_severe() -> None:
    found = problems(
        FakeSensor("a", result("a", Status.UNKNOWN)),
        FakeSensor("b", result("b", Status.OK)),
        FakeSensor("c", result("c", Status.INFO)),
        FakeSensor("d", result("d", Status.CRITICAL)),
        FakeSensor("e", result("e", Status.WARN)),
    )

    assert found == [("critical", "Title d"), ("warn", "Title e"), ("unknown", "Title a")]


def test_failing_items_replace_the_card_that_sums_them_up() -> None:
    items = (item("good", Status.OK), item("bad", Status.CRITICAL), item("meh", Status.WARN))

    found = problems(FakeSensor("a", result("a", Status.CRITICAL, items=items)))

    assert found == [("critical", "Title a · bad"), ("warn", "Title a · meh")]


def test_card_worse_than_its_items_is_listed_too() -> None:
    items = (item("meh", Status.WARN),)

    found = problems(FakeSensor("a", result("a", Status.CRITICAL, items=items)))

    assert found == [("critical", "Title a"), ("warn", "Title a · meh")]


def test_page_loads_only_its_own_relative_files() -> None:
    page = page_of(published(full_report()))

    assert re.findall(r'(?:src|href)="([^"]*)"', page) == ["static/page.css", "static/page.js"]
    for forbidden in ("style=", "<style", "<form", "onclick=", "javascript:"):
        assert forbidden not in page
    # The only script is the file of STETHO.
    assert re.findall(r"<script[^>]*>(.*?)</script>", page, re.DOTALL) == [""]


def test_refresh_failure_message_is_ready_but_hidden() -> None:
    page = page_of(Api())

    assert re.search(r'id="refresh-failed"[^>]*role="alert" hidden>The page can no longer', page)
    assert "<noscript>" in page


@pytest.mark.parametrize(
    ("name", "kind"), [("page.css", "text/css"), ("page.js", "text/javascript")]
)
def test_static_files_are_served(name: str, kind: str) -> None:
    answer = Api().get(f"/static/{name}")

    assert answer.status == 200
    assert answer.content_type.startswith(kind)
    assert answer.body == (STATIC / name).read_bytes()


@pytest.mark.parametrize("path", ["/", "/static/page.css", "/static/page.js"])
def test_page_and_files_need_the_password_and_take_no_parameter(path: str) -> None:
    assert Api().get(path, authenticated=False).status == 401
    assert Api().get(path, "x=1").status == 400


@pytest.mark.parametrize(
    "path",
    [
        "/static/",
        "/static/other.js",
        "/static/../openapi.yaml",
        "/static/..%2Fguard.py",
        "/static/page.js/x",
        "/index.html",
    ],
)
def test_no_other_file_is_served(path: str) -> None:
    assert Api().get(path).status == 404


def test_script_never_builds_html_from_text() -> None:
    script = (STATIC / "page.js").read_text(encoding="utf-8")

    for forbidden in (
        ".innerHTML",
        ".outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "new Function",
        "http://",
        "https://",
    ):
        assert forbidden not in script
    # The page is reloaded by a relative address, which works behind a prefix.
    assert 'fetch("./"' in script


def test_ages_are_counted_from_the_time_of_the_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("stetho.web.routes._now", lambda: NOW.replace(hour=15))

    page = page_of(published(run_probe(config())))

    assert ">3 h ago</time>" in page


def test_page_carries_the_mark_the_script_looks_for() -> None:
    script = (STATIC / "page.js").read_text(encoding="utf-8")

    assert '<main id="content" data-stetho-page>' in page_of(Api())
    assert 'querySelector("main#content[data-stetho-page]")' in script


def test_old_reading_time_grows_with_the_clock() -> None:
    translator = load_translator(Language.FR)
    report = run_probe(config())
    later = NOW.replace(hour=14)

    built = build_page(view(report, translator, stale=False), later, translator, config().timezone)

    assert built.checked is not None
    assert built.checked.relative == "il y a 2 h"
