# STETHO & NURSE

Monitoring for [Hermes Agent](https://github.com/NousResearch/hermes-agent): see at a glance
whether it works, get an alert when it breaks, and repair it with predefined actions. Hermes
itself is neither modified nor upgraded, and no LLM is involved.

> **Status: early development.** STETHO starts, checks the `gateway` module periodically and
> serves its state through a protected, read-only API. There is no status page yet, and NURSE
> does not exist.

## How it works

| Service | Role | Rights |
| --- | --- | --- |
| **STETHO** | Reads Hermes data, diagnoses, displays a status page and sends alerts | Reads data only; requests actions from NURSE |
| **NURSE** | Checks each request again, then runs actions from its catalogue | Not exposed; reaches Docker through a filtering proxy |

See the [architecture](docs/architecture.md) for the data flows and trust boundaries, and
[security](docs/features/security.md) for the limits of this model.

## Planned features

- Status page in French, English or Spanish (English by default).
- One module per monitored domain: gateway, platforms, scheduler, cron jobs, Kanban, sessions
  and known weaknesses.
- Alerts on Discord first; Telegram can be added later.
- Corrective actions, run with a button or, for the safest ones, automatically.

## Documentation

| Document | Content |
| --- | --- |
| [Documentation index](docs/README.md) | Where to start |
| [Architecture](docs/architecture.md) | Components and data flows |
| [Technology choices](docs/tech-stack.md) | What we use and why |
| [Features](docs/features/README.md) | One page per functional area, with decisions and open points |
| [Roadmap](docs/roadmap.md) | Order of the work |

## Getting started

Requirements: Git, [uv](https://docs.astral.sh/uv/) and `make`.

```bash
git clone git@github.com:N0AH-sve/Stetho-Nurse-.git
cd Stetho-Nurse-
make setup   # Python environment and Git hooks
make check   # lint, type check, tests
```

To start STETHO, copy `config/stetho.example.yaml` to `config/stetho.yaml`, which is never
committed, and adapt it. Outside a container, set `server.host` to `127.0.0.1`: the example
listens on every interface, and its password is empty.

```bash
uv run python -m stetho --config config/stetho.yaml
```

| Command | Effect |
| --- | --- |
| `make help` | List commands |
| `make setup` | Install the environment and Git hooks |
| `make fmt` | Format and fix the code |
| `make check` | Run `lint`, `typecheck` and `test` |
| `make hooks` | Run the pre-commit hooks on all files |
| `make audit` | Look for known vulnerabilities in dependencies |

## Repository layout

```
.claude/      shared Claude Code settings
.github/      continuous integration
config/       example configuration of both services
docs/         documentation
stetho/       STETHO code
tests/        tests and made-up Hermes data
Makefile      development commands
pyproject.toml, uv.lock   Python tools and locked versions
```

## Contributing

Read [How to contribute](CONTRIBUTING.md).
