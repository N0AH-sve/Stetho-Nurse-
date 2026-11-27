from datetime import UTC, datetime
from itertools import permutations, product
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from stetho.anomalies import AnomalyCode
from stetho.results import Detail, Item, SensorResult, Status, overall_status, worst_status

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
PROBLEMS = (Status.UNKNOWN, Status.WARN, Status.CRITICAL)  # least to most severe


def item(**changes: Any) -> Item:
    values: dict[str, Any] = {
        "id": "job-1",
        "label": "Daily report",
        "status": Status.OK,
        "summary": "Ran",
    }
    return Item(**(values | changes))


def sensor(**changes: Any) -> SensorResult:
    values: dict[str, Any] = {
        "id": "cron_jobs",
        "title": "Scheduled jobs",
        "group": "Tasks",
        "status": Status.OK,
        "summary": "All jobs ran",
        "checked_at": NOW,
    }
    return SensorResult(**(values | changes))


def test_statuses_are_the_five_of_the_format() -> None:
    assert {status.value for status in Status} == {"ok", "warn", "critical", "info", "unknown"}


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([Status.OK], Status.OK),
        ([Status.OK, Status.INFO], Status.OK),
        ([Status.OK, Status.UNKNOWN], Status.UNKNOWN),
        ([Status.UNKNOWN, Status.WARN, Status.OK], Status.WARN),
        ([Status.WARN, Status.CRITICAL, Status.UNKNOWN], Status.CRITICAL),
        ([Status.CRITICAL, Status.INFO], Status.CRITICAL),
    ],
)
def test_most_severe_status_wins_in_any_order(statuses: list[Status], expected: Status) -> None:
    for order in permutations(statuses):
        assert overall_status(order) is expected


@pytest.mark.parametrize("statuses", [[], [Status.INFO], [Status.INFO, Status.INFO]])
def test_nothing_checked_is_never_healthy(statuses: list[Status]) -> None:
    assert overall_status(statuses) is Status.UNKNOWN
    assert worst_status(statuses) is None


def test_statuses_can_come_from_a_generator() -> None:
    assert overall_status(status for status in (Status.OK, Status.WARN)) is Status.WARN


def test_complete_result_is_accepted() -> None:
    detail = Detail(label="Active agents", value="2")

    result = sensor(
        status=Status.WARN,
        details=(detail,),
        items=(item(status=Status.WARN, details=(detail,), actions=("RERUN_JOB",)),),
        actions=("RESTART_GATEWAY",),
        anomalies=(AnomalyCode.UNEXPECTED_VALUE,),
        checked_at=NOW.astimezone(ZoneInfo("Europe/Paris")),
    )

    assert result.items[0].actions == ("RERUN_JOB",)


@pytest.mark.parametrize(
    ("item_status", "sensor_status"),
    [
        (worst, status)
        for worst, status in product(PROBLEMS, Status)
        if status in (Status.OK, Status.INFO)
        or (status in PROBLEMS and PROBLEMS.index(status) < PROBLEMS.index(worst))
    ],
)
def test_sensor_cannot_hide_a_problem_of_an_item(
    item_status: Status, sensor_status: Status
) -> None:
    items = (item(), item(id="job-2", status=item_status))

    with pytest.raises(ValueError, match="less severe than its items"):
        sensor(status=sensor_status, items=items)


@pytest.mark.parametrize(
    ("item_status", "sensor_status"),
    [
        (worst, status)
        for worst, status in product(PROBLEMS, PROBLEMS)
        if PROBLEMS.index(status) >= PROBLEMS.index(worst)
    ],
)
def test_sensor_may_be_as_severe_or_more_than_its_items(
    item_status: Status, sensor_status: Status
) -> None:
    assert sensor(status=sensor_status, items=(item(status=item_status),))


@pytest.mark.parametrize("item_status", [Status.OK, Status.INFO])
@pytest.mark.parametrize("sensor_status", list(Status))
def test_healthy_or_info_items_do_not_limit_the_sensor(
    item_status: Status, sensor_status: Status
) -> None:
    assert sensor(status=sensor_status, items=(item(status=item_status),))


@pytest.mark.parametrize(
    "changes",
    [
        {"id": ""},
        {"id": "Cron"},
        {"id": "../x"},
        {"id": "1cron"},
        {"id": "a" * 65},
        {"title": " "},
        {"group": ""},
        {"summary": ""},
        {"advice": " "},
        {"checked_at": NOW.replace(tzinfo=None)},
        {"actions": ("restart",)},
        {"actions": ("RESTART GATEWAY",)},
        {"actions": ("A" * 65,)},
        {"actions": ("PAUSE_JOB", "PAUSE_JOB")},
        {"items": (item(), item())},
        {"anomalies": (AnomalyCode.PARSE_ERROR, AnomalyCode.PARSE_ERROR)},
    ],
)
def test_invalid_sensor_result_is_refused(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match=r".+"):
        sensor(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"id": ""},
        {"label": " "},
        {"summary": ""},
        {"advice": ""},
        {"actions": ("rm -rf",)},
        {"actions": ("RERUN_JOB", "RERUN_JOB")},
    ],
)
def test_invalid_item_is_refused(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match=r".+"):
        item(**changes)


def test_detail_needs_a_label_but_may_have_an_empty_value() -> None:
    assert Detail(label="Last error", value="").value == ""
    with pytest.raises(ValueError, match="label"):
        Detail(label="", value="x")


def test_error_never_quotes_a_value() -> None:
    marker = "made-up-private-text"

    for changes in ({"id": f"{marker}!"}, {"actions": (marker,)}):
        with pytest.raises(ValueError, match=r".+") as caught:
            sensor(**changes)
        assert marker not in str(caught.value)
