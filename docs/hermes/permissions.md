# File access

Reference version and conventions: [Hermes data](README.md).

## User of the Hermes image

The image creates a user `hermes` with UID 10000, which can be remapped at startup. Gateways run
as that user. Commands run with `docker exec` run as root unless a user is given.

## Modes set by the code

| File | Mode | How |
| --- | --- | --- |
| `cron/jobs.json` | `0600` | Built with `mkstemp`, then renamed; no other mode is applied |
| `cron/ticker_*` | `0600` | Explicit |
| `state.db`, `-wal`, `-shm` | `0600` | Applied when Hermes opens the database for writing, in containers too |
| `cron/deliveries.db` | `0600` | Applied at each connection |
| `cron/output/` files | `0600` | Explicit |
| `gateway_state.json`, `state/gateway.lifecycle.json`, `state/gateway.heartbeat` | Depends on the umask | An existing mode is kept |
| `logs/gateway-exit-diag.log` | Depends on the umask | |
| `cron/executions.db`, `kanban.db` | Depends on the umask | No mode found in the code |
| `.env` | `0600` | Applied at each container start |
| `auth.json` | `0600` | Applied when the container creates it |
| `config.yaml` | `0640` | Applied at each container start |

**Unverified:** the umask of the running services, the modes of the folders and of the SQLite
side files other than those of `state.db`. In a container, Hermes does not tighten folder modes
unless asked to.

## Consequence 1: a separate user cannot read the data

A reader running as a UID other than the owner of the Hermes files, and without extra
privileges, cannot read `cron/jobs.json`, the ticker markers, `state.db` or
`cron/deliveries.db`. This affects the scheduler, cron job and session modules, part of the
weaknesses module, and the target check of NURSE. The gateway files are not affected if the
umask leaves them readable.

Running STETHO or NURSE with the Hermes UID solves it, but then they can also read `.env` and
`auth.json`, which have the same owner. This conflicts with the requirement that
[STETHO cannot read secret files](../features/security.md#decisions).

Notes on the options:

| Option | Limit |
| --- | --- |
| Same UID, whole data folder mounted | Secret files are readable |
| Same UID, secret files hidden by other mounts | Fragile: **unverified** whether a mask survives when the file is replaced. New secret files would not be hidden |
| Same UID, only selected paths mounted | `state.db` and `gateway_state.json` sit next to the secrets; mounting single files breaks when they are replaced |
| ACL for a second user | An explicit `0600` clears the ACL mask, so the entry has no effect |
| Extra capability to read any file | Reads everything, secrets included |
| A small separate process with the Hermes UID that publishes selected data | More code and one more component to review |

No option is chosen yet.

## Consequence 2: `mode=ro` does not always work

**Reproduced** on a made-up database, from a directory and files without write permission
(SQLite 3.45.1, one user, `chmod` rather than a read-only mount):

| Situation | Result of `mode=ro` |
| --- | --- |
| A writer holds the database open; `-wal` and `-shm` exist | Opens |
| `-wal` exists without `-shm` | Opens |
| The writer closed the database: no `-wal`, no `-shm` | **Fails**: `attempt to write a readonly database` |

The Hermes image builds SQLite 3.53.4, so databases normally use WAL mode. Hermes closes the
connections to `cron/executions.db` and `cron/deliveries.db` after each transaction. When no
other process holds them, STETHO and NURSE should therefore expect the third case.
**Unverified** on the SQLite version of the image, on a real read-only mount, and for how long
`state.db` and `kanban.db` stay open.

Options to study:

| Option | Limit |
| --- | --- |
| `immutable=1` when there is no `-wal` | Wrong results if a writer starts during the read |
| Copy the database to a temporary folder, then read the copy | The copy may be inconsistent; costly for a large `state.db` |
| Mount the data with write access | Gives up the read-only guarantee |

Hermes may also use another journal mode, depending on its settings and on the file system.

## To check on a real installation

- UID and GID of the data folder, and the mode of each file listed above.
- Whether `-wal` and `-shm` exist when the cron databases are idle.
- Which secret files exist in the data folder and in each profile.
