# Contributing

## Workflow

- One short-lived branch per topic, off `main`.
- Commits follow [Conventional Commits](https://www.conventionalcommits.org/):
  `feat(scope): description`. A hook enforces this.
- `make check` passes before every commit.

## Language

Everything is written in English: code, identifiers, comments, documentation, tests, log
messages and commit messages. The only exception is the translations of the user-facing texts:
the page and the alerts are available in French, English and Spanish.

## Tests

- Error cases are tested as thoroughly as the happy path.
- No real Hermes data in the repository (identities, messages, secrets).

## Documentation

Update the matching page in `docs/features/` in the same change as the code.

## Dependencies

- Add with `uv add` (`uv add --dev` for a tool) and commit `uv.lock`.
- `exclude-newer` in `pyproject.toml` filters out very recent releases: move it forward to
  upgrade, keeping at least two weeks of distance.
