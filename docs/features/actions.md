# Corrective actions (NURSE)

> Status: the settings, the catalogue and the reception of [requests](#requests) are
> implemented, as well as the [checks in Hermes](#checks-in-hermes). No command is run.

## Purpose

Runs only the actions declared in its catalogue, after checking each request. The first
catalogue contains five actions.

## Design

- **Monolith.** NURSE is a single, small program. It has no modules and no plugin system.
- **Easy to extend.** Adding an action that runs a `hermes` command means adding an entry to
  `actions.toml`, with no code change, then restarting NURSE.
- **Code is needed only for a new capability**, such as a new way to check that a target exists
  or to verify the effect of an action.
- **Closed at runtime.** An action that is not in the catalogue is refused. Only the
  administrator edits the catalogue; STETHO can neither read nor change it.
- **Fixed guardrails** that no catalogue entry can lift: only the configured container, only
  the `hermes` command, no shell started by NURSE, never as root.

## Program

NURSE is the `nurse/` folder. It uses the **standard library only** and never imports STETHO,
which needs libraries NURSE must not have. A test checks both, and that the formats they share
stay the same. The initial specification aims at under 300 lines: this is a goal, and no check
is removed to reach it.

## Settings

[`nurse.example.toml`](../../config/nurse.example.toml) lists every setting; all are required.
`nurse/settings.py` checks them and stops NURSE on a missing, unknown or invalid one. No message
quotes a value.

| Setting | Rule |
| --- | --- |
| `auto_actions` | True or false: the switch of all automatic actions |
| `max_actions_per_hour` | 1 to 100, over all actions, whatever their origin |
| `hermes.container` | A Docker name: it becomes part of a path of the Docker API |
| `docker.proxy_url` | `http://host:port` with a port above 0, nothing else: no socket, credentials, path or query |
| Folders and catalogue | Absolute paths without `..` or a leading `//`. The data, prescription, result and log folders are separate, none inside another. The catalogue is neither inside one of them nor above it |

`log_dir` is **private** to NURSE: STETHO must neither read nor write it. **Limit:** the paths
are compared as written; links and the real mounts are checked at deployment.

## Catalogue

[`actions.example.toml`](../../config/actions.example.toml) holds the five actions and describes
each field. It is read at startup only.

| Field | Rule |
| --- | --- |
| Code | Capital letters, digits and `_` |
| `label` | `en` required; `fr` and `es` optional |
| `type` | `command` or `restart_container` |
| `argv` | 1 to 32 arguments. The first is exactly `hermes`. `{target}` is a whole argument, with no other brace, and never the second one |
| `user` | A user name or number, never `root` or 0, without group |
| `target` | `pattern` and `must_exist_in` (`cron_job` or `kanban_task`) |
| `verify` | `gateway_restarted`, `job_ran`, `job_paused` or `task_reclaimed`; it must match the target and the type |
| `auto` | Only `RESTART_GATEWAY` may be automatic, and only with `hermes gateway restart` exactly, verified by `gateway_restarted`, without target |
| `rate_limit` | Optional. If given, both `auto_per_hour` and `manual_per_hour` are required, from 0 to 100; without it, 2 and 5 |
| `timeout_seconds`, `verify_timeout_seconds` | Required, 1 to 3600 |

What must be true before running an action follows from `verify`: the gateway may be restarted,
the job is not running or is enabled, or the task is running.

**Limits.** An action cannot start another program than `hermes` without a change of the code.
The checks of the catalogue prevent mistakes; they do not make a catalogue edited by an attacker
safe, since a `hermes` command can do harm. Protect the file and review it like code.

**Targets.** A target `pattern` comes from the administrator. NURSE also limits every target to
1 to 64 letters, digits or `_`, the form of the API, so that it cannot be an option or a path.
Both must match the whole value. **Limit:** an expression that takes very long to evaluate is
not detected.

**Limit:** a `user` is checked as written: another name for the identifier 0 in the container would not be
seen.

## Decisions

- **Action list for the buttons.** NURSE publishes a summary in the results directory, which
  STETHO can only read: code, label and target type of each action, without the commands.
- **Action codes in the API.** The API accepts any well-formed code. NURSE refuses a code that
  is not in its catalogue.
- **Commands.** Only `hermes` commands, and the restart of the container. The initial
  specification refused shells; a list of shells is too easy to get around.
- **What may run unattended.** One exact command, written in the code. Changing it needs a
  change of the code. The container is never restarted automatically.
- **Rate limits.** Configurable for each action. Defaults per hour: 2 for automatic runs and 5
  for manual runs.
- **Automatic requests.** NURSE accepts a request marked automatic only if the catalogue allows
  it for that action. A total hourly cap applies whatever the origin, because STETHO could lie
  about it.
- **Two timeouts.** The command and the verification of its effect have separate timeouts.
- **Shared directories.** STETHO and NURSE run as different users and share a group with limited
  rights.

## Requests

`python -m nurse --config <file>` checks the requests. **It runs no action yet:** a request
that passes every check is refused with `execution_disabled`.

The prescription folder is written by STETHO, the exposed service: everything in it is hostile.
It is read every second, 20 files at most each time. Only names `<rx_id>.json` are considered;
other names and folders are left alone, so STETHO writes a request under a name starting with a
dot and then renames it. A request is a regular file without link, of 4 KiB at most. It is
deleted once handled: the private journal keeps its trace.

**STETHO must make its requests readable** by the shared group, whatever its umask: NURSE treats a
file it cannot read as invalid, and deletes it. The logs of the container need a size limit, since
each request writes a line.

### Checks, in order

The first check that fails gives the `refusal_reason`.

| Check | Code |
| --- | --- |
| The state of NURSE can be read | `state_unreadable` |
| The identifier has no result yet and was not handled in the last 10 minutes | `duplicate` |
| A prescription of the contract, named after its identifier, without other or repeated field | `invalid_request` |
| Created at most 5 minutes ago, and at most 1 minute ahead | `expired` |
| The action is in the catalogue | `unknown_action` |
| The request is not automatic: automatic actions come after the alerts | `auto_disabled` |
| A target only when the action has one, within [both limits](#catalogue) | `invalid_target` |
| [Hermes allows it](#checks-in-hermes) | `state_unreadable`, `target_not_found` or `not_relevant` |
| At least 120 s since the last restart, of either kind and any origin | `too_soon` |
| Under the hourly limits of the action and in total | `rate_limited` |
| An action may be run. **Not yet** | `execution_disabled` |

**Limits** count the actions that were started, whatever their result, over the last 60
minutes. A refused request does not count. Nothing is started yet, so only tests reach them.

### Checks in Hermes

`nurse/hermes.py` reads the main profile and the default Kanban board, without selecting any
content. It never asks to write. What cannot be read or understood gives `state_unreadable`.

| Action verified by | Allowed when | Otherwise |
| --- | --- | --- |
| `gateway_restarted` | `desired_state` in `gateway_state.json` is `running` | `not_relevant` if `stopped`; `state_unreadable` if absent or unknown. This is stricter than the buttons of STETHO |
| `job_paused` | The job of `cron/jobs.json` is enabled | `not_relevant`, or `target_not_found` |
| `job_ran` | The job is enabled, has no `run_claim` or `fire_claim`, and no `claimed` or `running` row in `cron/executions.db` | `not_relevant`, or `target_not_found` |
| `task_reclaimed` | The default board is active, the task is running, and its current attempt gave no sign of life for more than 15 minutes or passed its maximum duration | `not_relevant`, or `target_not_found` |

- Only the identifier is compared: the name of a job is never a target.
- Each action reads what it depends on: the gateway state is not read for a job or a task.
- The age and duration of a task are measured on its **current attempt**, so that the start or
  the heartbeat of an earlier one cannot make a new attempt look stuck.
- No link below the data folder is followed, side files of a database included, and a broken link
  is not an absence. A JSON file is limited to 4 MiB and a query to 2 s.
- Without `kanban/current`, the active board is the default one.
- A database with a `-journal` file is refused: it may hold changes that were never committed.
  **Limit:** in a journal mode that keeps this file, the actions that read it are always refused.
- A database without `-wal` or `-shm` is opened as `immutable`, and the reading is dropped if
  the file changes meanwhile; otherwise it is opened with `mode=ro`.

**Read-only mount required:** with `mode=ro` on a folder it may write, SQLite can create or change
the `-shm` and `-wal` files. The [deployment](deployment.md#decisions) must give a verified
read-only mount, which has not been tried.

**Limits.** Nothing was tried on a real installation. The environment variables of Hermes that
move the boards are not seen. Hermes may change
between the check and the action, the active board included. A claim or an execution left
unfinished after a crash blocks a rerun until Hermes clears it.

### State, results and journal

| Where | What |
| --- | --- |
| `log_dir/state.json` | Handled identifiers, started and pending actions, and the last entries. Saved before anything is answered. Created empty at the first start. Unusable, or missing after it, it refuses everything and is never overwritten |
| `log_dir/journal.jsonl` | One line per refused request: 5 files of 1 MiB at most |
| `log_dir/started.json` | The identifiers of the actions, kept 8 days after they end, and longer while the state lists them. Written before the state at each action. Created at the first start. Unusable, or missing after it, it refuses everything |
| `log_dir/initialized` | The marker of a completed first start, written after the two files above. Written again at startup if only it is lost |
| `log_dir/actions.jsonl` | One line per ended action, with the same limit. A refusal never writes here |
| `results_dir/<rx_id>.json` | The result, readable by the group. The first answer to an identifier is never replaced. Removed after 7 days. Above 1000, the oldest refusals are removed; the results of the actions in the log do not count |
| `results_dir/_log.json` | The last 80 actions and the last 20 refused requests, the most recent first. No identifier can take this name |

Results and the state are written atomically, and their folder is synced. Only one NURSE can run on
a private folder: it holds a lock there, and removes at startup the files left half written. With
20 000 identifiers to remember, requests are refused with `state_unreadable` until some are
forgotten: these refusals are neither remembered nor added to `_log.json`. With 1000 files that
cannot be handled, nothing more is tried, and NURSE logs it. Old results are removed
even without any request, as long as the prescription folder can be read. With an unusable state, only
the results that say they are refusals are removed. When the state or the journal cannot be written,
or a request cannot be deleted, the file is not tried again until NURSE restarts or the file
goes away. **Limits:** with an unusable state, `_log.json` is not updated. A stopped NURSE
leaves the requests waiting.

### Traces and interruptions

- **A flood of refused requests cannot erase the trace of an action:** actions have their own
  journal, their own place in the log, and their results do not count in the limit of 1000.
  **Limit:** it can still push other refusals out within seconds.
- **An action is recorded before it is run,** and stays pending until its answer is written. When
  NURSE starts, a pending action keeps the answer already written, or is answered `unverified`.
  It is never run again. If this cannot be written, NURSE does not start. The journal of the
  actions is synced to disk.
- **The identifiers of the started actions are kept for 8 days after the action ends,** and as
  long as the state lists the action among the pending ones or the last 80, in a file apart. A
  request that reuses one is a `duplicate` and never replaces its result, which does not count in
  the limit of 1000. A result itself goes after 7 days.
- **NURSE does not start with an unusable state:** it could not tell what was interrupted. It
  says so in its logs, and no request is answered until the state is repaired. A state that
  becomes unusable while NURSE runs refuses everything.
- **A lost state file is not a first start.** NURSE writes `state.json`, `started.json` and then
  the marker. While it runs, or once the marker is there, a missing state file is a loss, even
  when both are missing: it is never written again. Without the marker, `started.json` without
  `state.json`, or a missing `started.json` while the state lists actions, is a loss too;
  anything else is an interrupted first start, which is resumed.
- **To start again after a loss,** restore the missing file. Removing the three files is a new
  first start: the limits and the protection of the past actions are lost. **Limit:** NURSE
  cannot tell the loss of the three files from a first start.
- **Such a refusal never answers for an action.** The identifiers of the actions are then read
  apart, from `started.json`: their requests are journaled and deleted without any result file.
  If that file cannot be read either, or is missing, no result file is written at all: NURSE
  creates it at its first start. The same protection applies when too many identifiers are
  remembered. **Limit:** such requests get no answer on the page.
- A request whose action could not be recorded as started is later a `duplicate`, without result
  file. Nothing was run.
- A request that was only being refused when NURSE stopped is answered `duplicate`: nothing was
  run.
- Once the interrupted actions are answered, a public log that cannot be written does not stop
  NURSE: the next request writes it again.

No action is run yet: only tests record one.

## Decided, not implemented yet

Decided on 2026-11-27.

### Execution

- **Manual actions first.** Automatic requests stay refused until the alerts exist.
- **One action at a time.** A request received during an action is refused with `busy`, without
  queue. **Limit:** a long rerun also blocks an urgent restart.
- **Catalogue.** The five actions, without `gateway start`. A restart without effect is
  `unverified`.
- **Proof.** Restarts: a new process and a real sign of life later than the request. Rerun: a
  new **successful** run. Pause: the job is disabled. Kanban: a new run ended as `reclaimed`.
- **Long rerun.** Waited for 30 minutes at most, then `timeout`, never retried: the command may
  go on, since Docker offers no call to stop it.
- **Command output.** Only known verdict lines are kept, never free text, since a command can
  print a job name that comes from a prompt. The expected lines are in the code, by `verify`.
- **Total cap.** 7 actions per hour by default.
- **Docker proxy.** `wollomatic/socket-proxy` with a pinned image. Reading the description of
  the container is not allowed unless it proves necessary. **Remaining risk:** path rules cannot
  limit the start of an `exec` to the Hermes container.

### With the buttons

- **NURSE present.** NURSE refreshes the summary of its catalogue every 30 s, also during a long
  action. When it is missing or older than 2 minutes, no button is shown.
- **Spacing of restarts.** The page shows the 120 s between two restarts with a
  **countdown** until a restart may be asked again. NURSE stays the one that decides.

## Open points

- **Kanban boards.** How to act on another board than the default one.
- **Profiles.** How a job of another profile is targeted, to act beyond the main profile.
- **Coverage.** Which [conditions without a repair or without a rule](../hermes/coverage.md)
  need one.
