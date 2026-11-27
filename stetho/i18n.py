"""User-facing texts, one YAML file per language.

All the languages are checked together when they are loaded: a missing key or a different
placeholder stops STETHO, whatever the chosen language.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from string import Formatter
from types import MappingProxyType

from stetho.config import Language
from stetho.yamlfile import YamlFileError, load_yaml

TRANSLATIONS_DIR = Path(__file__).parent / "translations"


class TranslationError(Exception):
    """A translation file is invalid, or a text is asked for in the wrong way."""


@dataclass(frozen=True)
class _Text:
    template: str
    placeholders: frozenset[str]


type _Catalogue = Mapping[str, _Text]


@dataclass(frozen=True)
class Translator:
    """The texts of the language chosen at installation."""

    language: Language
    _texts: _Catalogue

    def text(self, key: str, /, **values: str | int) -> str:
        """Return a text with its placeholders filled. Values are never read as templates."""
        text = self._texts.get(key)
        if text is None:
            raise TranslationError(f"unknown text: {key}")
        if values.keys() != text.placeholders:
            expected = ", ".join(sorted(text.placeholders)) or "none"
            raise TranslationError(f"{key}: expected these placeholders: {expected}")
        return text.template.format_map(values)


def _parse_text(template: str, where: str) -> _Text:
    try:
        parts = list(Formatter().parse(template))
    except ValueError:
        raise TranslationError(f"{where}: unbalanced braces") from None
    placeholders: set[str] = set()
    for _, name, format_spec, conversion in parts:
        if name is None:
            continue
        # Plain names only: no `{0}`, `{a.b}`, `{a[0]}`, `{a!r}` or `{a:>8}`.
        if not name.isidentifier() or format_spec or conversion:
            raise TranslationError(f"{where}: a placeholder must be a plain name, such as {{name}}")
        placeholders.add(name)
    return _Text(template, frozenset(placeholders))


def _flatten(node: object, prefix: str, file_name: str, texts: dict[str, _Text]) -> None:
    where = f"{file_name}: {prefix or 'the file'}"
    if isinstance(node, str) and prefix:
        if not node.strip():
            raise TranslationError(f"{where}: empty text")
        texts[prefix] = _parse_text(node, where)
        return
    if not isinstance(node, dict) or not node:
        raise TranslationError(f"{where}: expected a text or a group of texts")
    for name, child in node.items():
        # YAML reads keys such as `no`, `on` or `1` as something else than a text.
        if not isinstance(name, str) or not name.isidentifier():
            raise TranslationError(f"{where}: invalid key, quote it or rename it")
        _flatten(child, f"{prefix}.{name}" if prefix else name, file_name, texts)


def _load_catalogue(directory: Path, language: Language) -> _Catalogue:
    file_name = f"{language}.yaml"
    try:
        document = load_yaml(directory / file_name)
    except YamlFileError as error:
        raise TranslationError(f"{file_name}: {error}") from None
    texts: dict[str, _Text] = {}
    _flatten(document, "", file_name, texts)
    return MappingProxyType(texts)


def _check_same_texts(reference: _Catalogue, catalogue: _Catalogue, language: Language) -> None:
    file_name = f"{language}.yaml"
    if missing := reference.keys() - catalogue.keys():
        raise TranslationError(f"{file_name}: missing texts: {', '.join(sorted(missing))}")
    if extra := catalogue.keys() - reference.keys():
        raise TranslationError(f"{file_name}: texts absent in English: {', '.join(sorted(extra))}")
    for key, text in reference.items():
        if catalogue[key].placeholders != text.placeholders:
            raise TranslationError(f"{file_name}: {key}: placeholders differ from English")


def load_translator(language: Language, directory: Path = TRANSLATIONS_DIR) -> Translator:
    """Load and check every language, then return the texts of the chosen one."""
    catalogues = {item: _load_catalogue(directory, item) for item in Language}
    for item, catalogue in catalogues.items():
        _check_same_texts(catalogues[Language.EN], catalogue, item)
    return Translator(language, catalogues[language])
