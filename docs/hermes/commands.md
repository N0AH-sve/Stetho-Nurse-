# Commands

Reference version and conventions: [Hermes data](README.md). **No command was run.**
Statements about s6, the Docker API and the proxy come from their documentation and are marked
as such. What each condition can be repaired with is in [coverage](coverage.md).

## Summary

| Command | Exit code 0 means | Safe proof of the effect |
| --- | --- | --- |
| `gateway restart`, `start`, `stop` | The request was passed on. Nothing more | A new gateway life, see below |
| `cron run <id>` | The job was found. It may not have run, or may have **failed** | The execution row |
| `cron pause <id>` | The job is recorded as paused | `enabled` is false |
| `cron resume <id>` | The job is recorded as scheduled | `enabled` is true |
| `kanban reclaim <id>` | The claim was released | A run with outcome `reclaimed` |
| `kanban unblock <id>` | The task left `blocked` or `scheduled` | The task status |
| `sessions prune` | Pruned, nothing to prune, **or cancelled** | Not covered |

**Never rely on the exit code alone.** Output text is in
[`tests/fixtures/hermes-0.21/commands/`](../../tests/fixtures/hermes-0.21/commands/); colours
and extra warnings are left out.

## Common points

- A command handler's non-zero return becomes the exit code. `gateway` commands ignore the
  return value and fail only through an explicit exit.
- Invalid arguments are rejected by the argument parser, with exit code 2.
- In the image, `hermes` on the `PATH` is a wrapper: started as root, it switches to the
  `hermes` user. It exits with 126 or 127 when it cannot. NURSE should still ask for the
  `hermes` user explicitly.
- Job and task commands read the current profile. Another profile needs to be selected.
  **Unverified:** the exact way to select it for each command.

## `hermes gateway restart`

Source: `hermes_cli/gateway.py`, `hermes_cli/service_manager.py`. Inside the image, the CLI
hands the request to the s6 supervisor.

1. It runs `s6-svc -t` on the service of the profile, which sends `SIGTERM`. It does not wait.
2. It writes `desired_state: "running"` into `gateway_state.json`.
3. It exits, without a success message.

| Case | Exit code |
| --- | --- |
| Request passed on | 0 |
| No s6 service for the profile, or `s6-svc` fails | 1 |
| Called from inside the gateway process | 1 |
| Profile served by another gateway, by configuration | 78 |

### A stopped gateway is not started

The [s6 documentation](https://skarnet.org/software/s6/s6-svc.html) states that `-t` does
nothing when the service is down: it does not bring it up. The command still exits with 0.

The service is down after `gateway stop`. The finish script of the image also stops
restarting a gateway that exits with code 0 or 78, a fatal configuration error. `gateway start`
runs `s6-svc -u`, which brings it up.

**Unverified:** which exit code follows each startup failure, and whether the gateway is
restarted after the `SIGTERM` of a restart, since a clean exit would leave it down.

### The CLI makes the state file look alive

While writing `desired_state`, the CLI also sets `updated_at` to the current time, **as an
integer of epoch seconds**, and keeps the previous `gateway_state`. A dead or frozen gateway
last recorded as `running` then looks `running` with a fresh heartbeat. `start` and `stop` do
the same.

So "`running` and a recent `updated_at`" does **not** prove that a restart worked. Better
evidence, written only by a gateway:

- `state/gateway.lifecycle.json` has a `started_at` later than the request;
- and `gateway_state.json` has a new `pid` or `start_time`, with `updated_at` as an ISO 8601
  string written after the request.

STETHO must accept both types of `updated_at`, and must not treat the integer form as a
heartbeat.

## `hermes gateway start` and `stop`

Same limits: `s6-svc -u` or `-d`, no wait, then `desired_state` set to `running` or
`stopped`. `stop` also marks the stop as intended. `start` can register a missing service.

## `hermes cron run <id>`

Source: `hermes_cli/cron.py`, `tools/cronjob_tools.py`, `cron/jobs.py`.

**From the command line, the job runs immediately, inside the command's own process.** The
command lasts as long as the whole job, which has no short upper bound. It does not merely
mark the job for the next scheduler tick.

| Case | Exit code | Last line |
| --- | --- | --- |
| Ran and succeeded | 0 | `Ran now: succeeded.` |
| Ran and **failed**, delivery failure included | 0 | `Ran now: failed.` |
| Job **paused or disabled**: not run | 0 | `Job is paused/disabled; resume it before running.` |
| Already claimed by a run: not run | 0 | `Job is already being fired by the scheduler; not run again.` |
| Unknown job, or ambiguous name | 1 | `Failed to run job: …` |

- Success can only be read from the output, or better from `cron/executions.db` and
  `jobs.json`. The output also prints the job name: match a whole line, never a substring.
- The command accepts a job **name** as well as an identifier. NURSE must pass an identifier
  that it found itself.
- A claim protects against a second start for 300 s. The list of running jobs is local to each
  process. **Unverified:** whether a run longer than 300 s can be started twice.
- **Special case:** for a delivery platform reached through a relay, the command forwards the
  request to the gateway instead. It then prints `It will run on the next scheduler tick.` That
  gateway route uses `trigger_job`, which **enables a paused job**. **Unverified:** whether the
  route refuses paused jobs before that.

## `hermes cron pause <id>` and `resume <id>`

| Case | Exit code | Output |
| --- | --- | --- |
| Paused or resumed | 0 | `Paused job: <name> (<id>)` or `Resumed job: <name> (<id>)` |
| Unknown job, ambiguous name, or refused | 1 | `Failed to pause job: …` |

- Pausing a paused job succeeds and overwrites `paused_at` and `paused_reason`.
- `pause` only changes the record. **Unverified:** it does not appear to stop a run in progress.
- After `resume`, a run whose time passed during the pause may start at the next tick,
  depending on the catch-up settings. Resuming a past one-time job fails.

## `hermes kanban reclaim <id> --reason <text>`

Source: `hermes_cli/kanban.py`, `hermes_cli/kanban_db.py`.

| Case | Exit code | Output |
| --- | --- | --- |
| Reclaimed | 0 | `Reclaimed <id>` |
| Not running, or unknown identifier | 1 | `cannot reclaim <id> (not running or unknown id)` on stderr |
| Other refused operation | 1 | `kanban: …` on stderr |

Effects:

- It signals the worker when it belongs to the same host: `SIGTERM`, then `SIGKILL`.
- The task goes back to `ready`, or to `review` if it was being reviewed. **The dispatcher may
  start it again.**
- The run ends with outcome `reclaimed`, and the **failure counter is reset**, which gives the
  task a new set of retries.
- In the code read, the reclaim goes on even if the worker could not be stopped, so two workers
  could run. **Unverified** in practice.

It does **not** unblock a task in `blocked` that holds no claim.

Without `--board`, the command uses the active board. A task of another board needs
`hermes kanban --board <slug> reclaim <id>`.

To check the effect, look for the new `reclaimed` run: the status may already be `running`
again.

## `hermes kanban unblock <id>…`

Moves tasks from `blocked` or `scheduled` to `ready`, `todo` or `review`. It resets the failure
counter and the last error, and keeps the block type. It accepts several identifiers. A failure
prints `cannot unblock <id> (not blocked/scheduled?)` with exit code 1.

## `hermes sessions prune`

**Deletes data.** Without filter, it targets ended sessions older than 90 days. **Any filter
removes that age limit.** Pinned and archived sessions are kept by default.

| Case | Exit code |
| --- | --- |
| Pruned, or no session matches | 0 |
| No `--yes` and no terminal: `Cancelled.` | 0 |
| Another Hermes process holds `state.db`, without `--force` or `--dry-run` | 1 |
| Invalid filter, or database that cannot be opened | 1 |

So it is expected to be refused while the gateway runs. Its help warns that `--force` can leave
agents refusing requests until all writers stop. `--dry-run` only lists.

## Docker API calls

From the [Engine API 1.47 description](https://github.com/moby/moby/blob/v27.5.1/api/swagger.yaml).

| Need | Call |
| --- | --- |
| Read the container state | `GET /containers/{name}/json` |
| Prepare a command | `POST /containers/{name}/exec` |
| Start it | `POST /exec/{id}/start` |
| Read its exit code | `GET /exec/{id}/json` (`Running`, `ExitCode`) |
| Restart the container | `POST /containers/{name}/restart` |

- The description has **no call to stop a running command**, and does not say what happens
  when the client disconnects. A NURSE timeout must not assume that the command stopped.
- A restart answers 204 when done. It says nothing about the health of Hermes. On boot, the
  image starts a gateway only if its recorded intent is `running`, so a gateway stopped on
  purpose, or failed at startup without that intent, stays down.

### Proxy rules

From the [socket-proxy documentation](https://github.com/wollomatic/socket-proxy): one
expression per HTTP method, anchored automatically. Group alternatives in parentheses. Only
method and path are filtered. **Unverified:** how the API version prefix of the paths is
handled; the rules must include what the client really sends.

Two limits of path filtering:

- The `/exec/{id}/…` paths **do not name the container**. A rule cannot limit them to Hermes by
  path.
- The container description returned by `GET /containers/{name}/json` includes its
  configuration, environment variables included, which **may hold secrets**.

## To check on a real installation

- Behaviour of `gateway restart` under s6, for a running, frozen and stopped gateway.
- Real durations, and the exact output with warnings and colours.
- Exit code after each kind of startup failure.
- What happens to a command when the client disconnects.
