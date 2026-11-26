# Hermes v0.21.4 test data

Made-up data that follows the formats in [`docs/hermes/`](../../../docs/hermes/README.md).
It was written from the source code: none of it comes from a real installation.

## Reference time

All scenarios assume that it is **2026-01-15T12:00:00Z** (epoch `1768478400`). Tests inject
this clock.

## Scenarios

Each folder mirrors a part of a Hermes home.

| Folder | Content |
| --- | --- |
| `healthy/` | Everything works; includes a paused job |
| `degraded/` | Platform errors, unclean previous stop, failing scheduler, jobs, tasks and sessions |
| `stopped/` | Clean stop |
| `startup-failed/` | Startup failure |
| `invalid/` | One case per sub-folder: broken, unknown, missing or implausible input |

`invalid/jobs-bare-list/` is a shape that Hermes accepts and repairs.

## Databases

Git ignores `*.db`, so databases are described in SQL and built by the tests. Run the files of
`schema/` first, then the rows of the scenario:

| Database | Schema files | Rows |
| --- | --- | --- |
| `cron/executions.db` | `executions.sql`, `incidents.sql` | `cron/executions.sql` |
| `cron/deliveries.db` | `deliveries.sql` | `cron/deliveries.sql` |
| `kanban.db` | `kanban.sql` | `kanban.sql` |
| `state.db` | `state.sql` | `state.sql` |

The table definitions in `schema/` come from Hermes Agent (MIT License). Indexes, triggers,
search tables and tables that are not read are left out.

Texts starting with `MADE-UP` stand for message content: they must never appear in a result, a
page, a log or an alert.

## Not covered

File owners and modes, WAL side files, named profiles, additional Kanban boards and command
outputs. Tests create the first four when they need them.
