from pathlib import Path

import pytest

from stetho.config import Language
from stetho.i18n import TRANSLATIONS_DIR, TranslationError, load_translator
from stetho.results import Status

ENGLISH = 'greeting:\n  named: "Hello {name}, {count} left {{literally}}"\n  plain: "Hello"\n'


def catalogues(tmp_path: Path, **replaced: str) -> Path:
    """Write three identical catalogues, then replace the given ones."""
    for language in Language:
        content = replaced.get(language.value, ENGLISH)
        (tmp_path / f"{language}.yaml").write_text(content, encoding="utf-8")
    return tmp_path


def load_error(directory: Path, language: Language = Language.EN) -> str:
    with pytest.raises(TranslationError) as caught:
        load_translator(language, directory)
    return str(caught.value)


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        (Language.EN, "Everything is working"),
        (Language.FR, "Tout fonctionne"),
        (Language.ES, "Todo funciona"),
    ],
)
def test_shipped_translations_are_complete(language: Language, expected: str) -> None:
    assert load_translator(language).text("banner.all_ok") == expected


def test_shipped_directory_holds_one_file_per_language() -> None:
    names = {path.name for path in TRANSLATIONS_DIR.iterdir()}

    assert names == {f"{language}.yaml" for language in Language}


@pytest.mark.parametrize("language", list(Language))
@pytest.mark.parametrize("status", list(Status))
def test_every_status_has_a_name(language: Language, status: Status) -> None:
    assert load_translator(language).text(f"status.{status}")


def test_placeholders_are_filled(tmp_path: Path) -> None:
    translator = load_translator(Language.FR, catalogues(tmp_path))

    assert translator.text("greeting.named", name="Ada", count=2) == (
        "Hello Ada, 2 left {literally}"
    )


def test_values_are_not_read_as_templates(tmp_path: Path) -> None:
    translator = load_translator(Language.EN, catalogues(tmp_path))

    result = translator.text("greeting.named", name="{count} {0.__class__}", count=1)

    assert result.startswith("Hello {count} {0.__class__}, 1 left")


def test_placeholder_may_be_called_key(tmp_path: Path) -> None:
    for language in Language:
        (tmp_path / f"{language}.yaml").write_text('a: "{key}"\n', encoding="utf-8")

    assert load_translator(Language.EN, tmp_path).text("a", key="value") == "value"


@pytest.mark.parametrize(
    ("key", "values"),
    [
        ("greeting.absent", {}),
        ("greeting", {}),
        ("greeting.named", {}),
        ("greeting.named", {"name": "Ada"}),
        ("greeting.named", {"name": "Ada", "count": 1, "other": 2}),
        ("greeting.plain", {"name": "Ada"}),
    ],
)
def test_wrong_request_is_refused(tmp_path: Path, key: str, values: dict[str, str | int]) -> None:
    translator = load_translator(Language.EN, catalogues(tmp_path))

    with pytest.raises(TranslationError, match=key):
        translator.text(key, **values)


def test_expected_placeholders_are_given(tmp_path: Path) -> None:
    translator = load_translator(Language.EN, catalogues(tmp_path))

    assert translator.placeholders("greeting.named") == {"name", "count"}
    with pytest.raises(TranslationError, match=r"unknown text: greeting\.absent"):
        translator.placeholders("greeting.absent")


@pytest.mark.parametrize("chosen", list(Language))
@pytest.mark.parametrize(
    ("content", "message"),
    [
        ('greeting:\n  plain: "Salut"\n', "fr.yaml: missing texts: greeting.named"),
        (ENGLISH + 'extra: "x"\n', "fr.yaml: texts absent in English: extra"),
        (ENGLISH.replace("{count}", "{total}"), "fr.yaml: greeting.named: placeholders differ"),
        (ENGLISH.replace(", {count} left", ""), "fr.yaml: greeting.named: placeholders differ"),
    ],
)
def test_incomplete_language_stops_every_choice(
    tmp_path: Path, chosen: Language, content: str, message: str
) -> None:
    assert load_error(catalogues(tmp_path, fr=content), chosen).startswith(message)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("", "the file: expected a text or a group"),
        ('"just a text"\n', "the file: expected a text or a group"),
        ("a: {}\n", "a: expected a text or a group"),
        ("a: 3\n", "a: expected a text or a group"),
        ("a: [x]\n", "a: expected a text or a group"),
        ('a: "  "\n', "a: empty text"),
        ('no: "x"\n', "the file: invalid key"),
        ('"a.b": "x"\n', "the file: invalid key"),
        ('a: "{"\n', "a: unbalanced braces"),
        ('a: "}"\n', "a: unbalanced braces"),
        ('a: "{}"\n', "a: a placeholder must be a plain name"),
        ('a: "{0}"\n', "a: a placeholder must be a plain name"),
        ('a: "{x.y}"\n', "a: a placeholder must be a plain name"),
        ('a: "{x[0]}"\n', "a: a placeholder must be a plain name"),
        ('a: "{x!r}"\n', "a: a placeholder must be a plain name"),
        ('a: "{x:>8}"\n', "a: a placeholder must be a plain name"),
        ('a: "x"\na: "y"\n', "repeated key at line 2"),
        ('a: "x\n', "invalid YAML"),
    ],
)
def test_invalid_file_is_refused_and_named(tmp_path: Path, content: str, message: str) -> None:
    error = load_error(catalogues(tmp_path, es=content))

    assert error.startswith("es.yaml: ")
    assert message in error


def test_missing_language_file_is_refused(tmp_path: Path) -> None:
    directory = catalogues(tmp_path)
    (directory / "es.yaml").unlink()

    assert load_error(directory).startswith("es.yaml: cannot read the file")
