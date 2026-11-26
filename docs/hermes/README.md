# Hermes data

What STETHO and NURSE can expect to find in the Hermes data directory.

## Reference

| | |
| --- | --- |
| Version | Hermes Agent v0.21.4 |
| Source | [`NousResearch/hermes-agent`](https://github.com/NousResearch/hermes-agent), tag `v2026.9.21` |
| Commit | `d337b736aa1e8ebecfab043842d13e4a2d2f48a3` |

## Method

These pages come from **reading the source code**. No Hermes installation, instance or server
was used, and no Hermes code was run.

- Unless marked otherwise, a statement was read in the source.
- **Reproduced** means checked with our own script on made-up data, outside Hermes.
- **Unverified** means that reading the source is not enough.

A real installation may differ: settings, environment, older files or a modified image.

## Pages

| Page | Content |
| --- | --- |
| [Gateway](gateway.md) | State, platforms, heartbeat and stops |
| [Cron](cron.md) | Scheduler, jobs, executions, incidents and deliveries |
| [Kanban](kanban.md) | Boards, tasks and attempts |
| [Sessions](sessions.md) | Session database |
| [File access](permissions.md) | File modes and read-only access to SQLite |

Made-up examples are in [`tests/fixtures/hermes-0.21/`](../../tests/fixtures/hermes-0.21/).

## Blocking findings

Both are detailed in [file access](permissions.md).

1. Several files that must be monitored are readable by their owner only.
2. A SQLite database in WAL mode that no process holds open cannot be opened with `mode=ro` from
   a read-only directory.

## Not covered yet

- Exit codes and output of the commands that NURSE will run.
- Real owners, modes and mounts of an installation.
- Files only needed by the known weaknesses checks, such as skill backups and output folders.
