# Kanban files

Reference version and conventions: [Hermes data](README.md). Sources: `hermes_cli/kanban_db*.py`.

## Location

Boards are shared by all profiles and live under the **root** Hermes home.

| Board | Database |
| --- | --- |
| Default | `kanban.db` |
| Others | `kanban/boards/<slug>/kanban.db` |

A board folder may hold only `board.json`, with no database yet. `kanban/current` holds the slug
of the active board. Environment variables of Hermes can move these paths.

## Table `tasks`

Dates are **epoch seconds** (integers). Identifiers are `t_` followed by 8 hexadecimal
characters.

`status`: `triage`, `todo`, `scheduled`, `ready`, `running`, `blocked`, `review`, `done`,
`archived`.

Fields useful to monitoring: `id`, `title`, `assignee`, `status`, `started_at`,
`last_heartbeat_at`, `max_runtime_seconds`, `consecutive_failures`, `last_failure_error`,
`block_kind` (`dependency`, `needs_input`, `capability`, `transient` or null).

`body` and `result` are content: do not select them.

## Table `task_runs`

One row per attempt. `outcome` is null while the attempt runs.

Values of `outcome` found by static analysis of the calls that end a run:

`completed`, `blocked`, `review_requested`, `changes_requested`, `scheduled`, `reclaimed`,
`stale`, `timed_out`, `crashed`, `rate_limited`, `spawn_failed`, `gave_up`.

- The comments in the schema list fewer values than the code writes.
- Some calls pass the value through a variable: the list may be incomplete.
- `status` often equals `outcome`, but not always: for example `completed` gives `done`.
