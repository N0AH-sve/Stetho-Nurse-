"""Safe loading of a YAML file, with errors that never quote its content.

PyYAML error messages copy the faulty line, which may hold a secret. Only the kind of problem
and its position are kept.
"""

from collections.abc import Hashable
from pathlib import Path

import yaml

MAX_BYTES = 256 * 1024


class YamlFileError(Exception):
    """The file cannot be read or parsed. The message is safe to log."""


class _DuplicateKeyError(yaml.MarkedYAMLError):
    pass


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe loader that refuses a repeated key instead of keeping the last value."""

    def construct_mapping(
        self, node: yaml.MappingNode, deep: bool = False
    ) -> dict[Hashable, object]:
        seen: set[tuple[str, str]] = set()
        for key_node, _ in node.value:
            if isinstance(key_node, yaml.ScalarNode):
                key = (key_node.tag, key_node.value)
                if key in seen:
                    raise _DuplicateKeyError(problem_mark=key_node.start_mark)
                seen.add(key)
        return super().construct_mapping(node, deep=deep)


def _position(error: yaml.YAMLError) -> str:
    if isinstance(error, yaml.MarkedYAMLError) and error.problem_mark is not None:
        return f" at line {error.problem_mark.line + 1}, column {error.problem_mark.column + 1}"
    return ""


def load_yaml(path: Path) -> object:
    """Return the content of a UTF-8 YAML file holding a single document."""
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
    except OSError as error:
        raise YamlFileError(f"cannot read the file: {error.strerror}") from None
    if len(raw) > MAX_BYTES:
        raise YamlFileError(f"the file is larger than {MAX_BYTES} bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise YamlFileError("the file is not valid UTF-8") from None
    try:
        # A subclass of SafeLoader: it cannot build arbitrary objects.
        return yaml.load(text, Loader=_UniqueKeyLoader)  # noqa: S506
    except _DuplicateKeyError as error:
        raise YamlFileError(f"repeated key{_position(error)}") from None
    except yaml.YAMLError as error:
        raise YamlFileError(f"invalid YAML{_position(error)}") from None
    except (ValueError, RecursionError):
        # Raised for an impossible date or too deeply nested content.
        raise YamlFileError("invalid YAML value or structure") from None
