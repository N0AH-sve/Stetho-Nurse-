# Corrective actions (NURSE)

> Status: the settings and the catalogue are implemented. NURSE cannot be started yet: no
> request is read and no command is run.

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

## Planned content

- Prescription and result formats
- Checks, in order
- The five actions and their verification
- Manual and automatic modes, rate limits

Decided on 2026-11-27 and **not implemented yet**:

- **Total cap.** 7 actions per hour by default. Automatic actions are off by default.
- **Hostile requests.** The prescription folder is written by STETHO, the exposed service.
  Only regular files without link, of 4 KiB at most, named `<rx_id>.json`, are considered.
  Results are written atomically. The folder is read every second, 20 files at most each time.
  A request older than 5 minutes, or dated more than 1 minute ahead, is refused.
- **Replays.** Handled identifiers and rate counters are kept in the private folder of NURSE and
  survive a restart. If this state cannot be read, everything is refused.
- **Nothing unverified runs.** NURSE refuses when it cannot read the target or the state of the
  gateway, and refuses a restart when the intent is `stopped`, absent or unknown. For an absent
  intent, this is stricter than the buttons of STETHO.
- **Invalid requests** are logged with `prescription: null` and the reason.
- **Catalogue.** The five actions, without `gateway start`. A restart without effect is
  `unverified`.
- **Proof.** Restarts: a new process and a real sign of life later than the request. Rerun: a
  new **successful** run. Pause: the job is disabled. Kanban: a new run ended as `reclaimed`.
- **Long rerun.** Waited for 30 minutes at most, then `timeout`, never retried: the command may
  go on, since Docker offers no call to stop it. One action runs at a time.
- **Docker proxy.** `wollomatic/socket-proxy` with a pinned image. Reading the description of
  the container is not allowed unless it proves necessary. **Remaining risk:** path rules cannot
  limit the start of an `exec` to the Hermes container.

## Open points

- **Kanban board.** A task of another board needs the board to be selected.
- **Profiles.** How a job of another profile is targeted.
- **Coverage.** Which [conditions without a repair or without a rule](../hermes/coverage.md)
  need one.
