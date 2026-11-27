from collections.abc import Iterator
from pathlib import Path

import pytest

from stetho.errors import ErrorSummary, summarise_error
from stetho.results import Status, worst_status

MARKER = "made-up-private-text"


def failing_statuses() -> Iterator[Status]:
    yield Status.OK
    raise KeyError(MARKER)


def error_raised_through_stetho() -> BaseException:
    try:
        worst_status(failing_statuses())
    except KeyError as error:
        return error
    pytest.fail("no error was raised")


def test_only_type_and_stetho_calls_are_kept() -> None:
    summary = summarise_error(error_raised_through_stetho())

    assert summary.error_type == "KeyError"
    assert {call.file for call in summary.calls} == {"stetho/results.py"}
    assert summary.calls[0].function == "worst_status"
    assert all(call.line > 0 for call in summary.calls)


def test_message_and_other_paths_are_never_kept() -> None:
    summary = summarise_error(error_raised_through_stetho())

    for text in (str(summary), repr(summary)):
        assert MARKER not in text
        assert str(Path(__file__).parent) not in text
        assert "test_errors" not in text


def test_short_form_shows_the_closest_call() -> None:
    summary = summarise_error(error_raised_through_stetho())

    assert str(summary) == f"KeyError · {summary.calls[-1]}"
    assert str(summary.calls[-1]).startswith("stetho/results.py:")


def test_calls_can_be_taken_from_another_package() -> None:
    summary = summarise_error(error_raised_through_stetho(), Path(__file__).parent)

    assert [call.function for call in summary.calls][-1] == "failing_statuses"


def test_error_that_was_never_raised_has_no_call() -> None:
    summary = summarise_error(ValueError(MARKER))

    assert summary == ErrorSummary("ValueError", ())
    assert str(summary) == "ValueError"


def test_unusual_type_name_is_replaced() -> None:
    strange = type(f"{MARKER} !", (Exception,), {})

    assert summarise_error(strange()).error_type == "Exception"
