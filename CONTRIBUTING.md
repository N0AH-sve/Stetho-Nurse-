# How to contribute

## Before you start

- Read the [architecture](docs/architecture.md) and the page of the feature you work on in
  [`docs/features/`](docs/features/README.md).
- `docs/` records the decisions of the project. The initial specification is not versioned;
  if the two disagree, `docs/` wins.
- If a page lists an **open point** that affects your work, get it settled before coding, then
  record the decision in the same page.

## Set up

You need Git, [uv](https://docs.astral.sh/uv/) and `make`. uv uses Python 3.12.

```bash
make setup
make check
```

## Make a change

1. Start from an up-to-date `main` with a short-lived branch, one topic per branch:

   ```bash
   git switch main && git pull
   git switch -c feat/gateway-reader
   ```

   Name branches `<type>/<short-description>`, using the commit types below.

2. Make the change, with its tests and documentation.
3. Check it:

   ```bash
   make fmt
   make check
   make hooks
   ```

4. Commit and push, then open a pull request on GitHub.

There is no continuous integration yet: run the checks yourself before asking for a review.

## Commit messages

We follow [Conventional Commits](https://www.conventionalcommits.org/):
`type(scope): description`. The scope is optional. A hook rejects other formats.

Types: `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `build`, `ci`, `chore`, `security`,
`revert`.

Example: `feat(stetho): read the gateway state`

## Pull requests

- Keep them small, with one topic.
- In the description, say what changes, why, and how you checked it.

### Stacked pull requests

When a change depends on one that is not merged yet:

1. Branch from the previous branch instead of `main`.
2. Open the pull request with that branch as its base, and state its position, such as
   "Stack 2/3".
3. Merge from the bottom up, with **Create a merge commit**.
4. Delete each merged branch, then check that the next pull request now targets `main`.

## Language

Everything is written in English: code, identifiers, comments, documentation, tests, log
messages and commit messages. The only exception is the translations of the user-facing texts:
the page and the alerts are available in French, English and Spanish. User-facing texts are
never hard-coded.

## Code and tests

- Ruff handles style and mypy runs in strict mode.
- Test error cases as thoroughly as the happy path.
- A check that cannot be performed must report `unknown`, never `ok`.
- Nothing specific to an installation is hard-coded.
- Settings come from configuration files, never from environment variables. A new setting is
  added to the example file in `config/` in the same change.

## Security

The repository is public.

- Real configuration files stay local; only the `*.example.*` files are committed, with no
  secret.
- Never commit secrets, server addresses, domain names or host paths of a real installation.
- Never commit real Hermes data (identities, messages, databases, logs). Write test fixtures
  by hand.
- Never develop or test against a Hermes installation that is in use.
- Read [security](docs/features/security.md) before changing NURSE or its rights.
- Do not report a vulnerability in a public issue; contact the repository owner privately.

## Documentation

Update the matching page in `docs/features/` in the same change as the code. A new technology
or a change of one goes in [technology choices](docs/tech-stack.md).

## Dependencies

- NURSE uses the Python standard library only.
- Add with `uv add` (`uv add --dev` for a tool), explain why in the pull request, and commit
  `uv.lock`.
- `exclude-newer` in `pyproject.toml` filters out recent releases: move it forward to upgrade,
  keeping at least two weeks of distance.
- Run `make audit` after changing dependencies.
