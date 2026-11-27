# Gateway

> Status: the reader, the rules, the texts and the sensor are implemented. The
> [probe](probe.md#probe) runs the sensor and the [API](api.md#reading-routes) serves its result,
> and the [status page](status-page.md) shows it.

## Purpose

Tells whether the Hermes gateway is alive and why it last stopped.

## Sources

Formats are described in [gateway files](../hermes/gateway.md). Both are only read.

| File | Use |
| --- | --- |
| `gateway_state.json` | State of the gateway. Needed: without it, nothing can be told |
| `logs/gateway-exit-diag.log` | Last unclean stop: date and possible lack of memory. Optional history |

`.clean_shutdown`, `state/gateway.lifecycle.json` and `state/gateway.heartbeat` are not read.
The log holds no cause: STETHO shows when the next start noticed the unclean stop, and whether a
lack of memory is suspected.

## Reader

`stetho/modules/gateway/reader.py` turns the files into neutral data.

- **Adapter.** `adapters/hermes-0.21.yaml` lists the required and known fields, the states, the
  intents and the known exit reasons. There is one adapter, so no version is chosen yet. The
  Hermes version found in the file is kept if it is a short and simple text.
- **Free text.** `exit_reason` is kept only if the adapter lists it; otherwise the reader only
  says that there is another reason. `argv`, `hermes_home`, tracebacks and error messages are
  never kept. An unknown field name is filtered like a
  [received value](anomalies.md#what-is-kept-from-outside), and shown as `<hidden>` if needed.
- **Date.** `updated_at` is an ISO 8601 text with a time zone, or a whole number of seconds.
  The number is marked as written by a command: it
  [is not a sign of life](../hermes/commands.md#the-cli-makes-the-state-file-look-alive).
  A date before 2020 or more than 5 minutes in the future is not used.
- **Platforms.** The `platforms` field is left to the [platforms](platforms.md) module. Unlike
  in the initial specification, the gateway does not require it.
- **Log.** Only its last 256 KiB are read, so an older unclean stop is not seen. A last line
  without a newline is still being written and is ignored.

### Problems

| Problem | Anomaly | Result |
| --- | --- | --- |
| State file missing, unreadable or not a JSON object | `source_missing`, `source_unreadable` or `parse_error` | No state |
| `gateway_state` or `updated_at` absent | `schema_changed` | No state |
| Unknown `gateway_state` or `desired_state` | `unexpected_value` | Value not used |
| Date or number of agents impossible | `implausible_value` | Value not used |
| Wrong type | `schema_changed` | Value not used |
| Unknown top-level field | `schema_changed` | Ignored |
| Log missing | None: the history was never written | No unclean stop |
| Log unreadable | `source_unreadable` | The state is still read |
| Broken complete line in the log | `parse_error`, until the line leaves the part that is read | Other lines are read |

A wrong `gateway_state` type counts as an unknown value. An unknown intent is told apart from
an absent one, so that no repair relies on it. The [reading retries](anomalies.md#reading-retries)
apply to both files. A file larger than 1 MiB, a link or anything but a regular file is
unreadable. Only the file itself is checked for a link, not its parent folders.

### Profiles

The main profile (`default`) is at the root of the data directory, the others in
`profiles/<name>/`.

- With `profiles: auto`, the main profile is always expected. Another one is read only if it has
  a `gateway_state.json`. A folder whose name cannot be a profile name is reported.
- A listed profile is always expected, except when the main gateway says it serves it
  (`served_profiles`) and it has no state file of its own. The main file is read for this even
  if `default` is not listed.
- **Limit.** With `auto`, the removal of the whole state file of a secondary profile cannot be
  noticed after a restart of STETHO.

## Rules

[`rules.yaml`](../../stetho/modules/gateway/rules.yaml) holds the thresholds, the rules, their
status, text and actions. The first rule that matches wins. `rules.py` applies them. The age is
the time since `updated_at`; "more than" is strict.

| Order | Case | Status | Action offered |
| --- | --- | --- | --- |
| 1 | `stopped` with `desired_state: stopped` | `critical` | None: the stop is deliberate |
| 2 | `stopped` | `critical` | `RESTART_GATEWAY` |
| 3 | `startup_failed` | `critical` | `RESTART_GATEWAY` |
| 4 | `degraded` with a watchdog reason: the process is dead | `critical` | `RESTART_GATEWAY` |
| 5 | Date written by a command, more than 5 minutes old | `critical` | `RESTART_CONTAINER` |
| 6 | Date written by a command, more than 2 minutes old | `warn` | None |
| 7 | Other date written by a command | `unknown` | None |
| 8 | Date more than 5 minutes old | `critical` | `RESTART_CONTAINER` |
| 9 | Date more than 2 minutes old | `warn` | None |
| 10 | No usable date | `unknown` | None |
| 11 | `degraded` | `warn` | `RESTART_GATEWAY` |
| 12 | `starting` or `draining` | `warn` | None |
| 13 | `running` | `ok` | None |
| 14 | Unknown state | `warn` | None |

Rules 1 to 4 ignore the age, so that a stopped gateway is not announced as not answering.

Without a usable state file:

| Case | Status | Action offered |
| --- | --- | --- |
| State file missing for 5 minutes or less | `unknown` | None |
| State file missing for more than 5 minutes | `critical` | `RESTART_CONTAINER` |
| State file unreadable, invalid or without a required field | `unknown` | None |

Two protections are in the code, so that no change of the rules can lift them:

- **Intent.** No action is offered, in any state, when `desired_state` is `stopped` or holds a
  value STETHO does not know. When it is absent, actions stay offered.
- **Unknown value.** With an `unexpected_value` anomaly, the status is at least `warn`.

`RESTART_GATEWAY` may do nothing on a stopped gateway: to be settled with NURSE.

### Advice

Each rule may name an advice, a text of the translation files under `gateway.advice`. It says
what to do by hand: no button exists yet, and it never relies on one.

| Case | Advice |
| --- | --- |
| Deliberate stop | Start Hermes again if the stop is no longer wanted |
| `stopped` | Read the logs, then start the gateway again |
| `startup_failed`, watchdog | Read the logs, fix the cause, then start the gateway again |
| No sign of life, with or without a command, for more than 5 minutes | Check the container and its logs; restart it if needed |
| Command less than 5 minutes old, `starting`, `draining` | Wait, then read the logs if nothing changes |
| Late sign of life | Wait, then read the logs and check the load of the server |
| No usable date | Check the clock of the server and the Hermes version |
| `degraded` | Check the platform connections and the logs |
| Unknown state, or unknown value on a healthy gateway | Check the Hermes version |
| State file missing | Check that Hermes runs and its data folder is mounted |
| State file unusable | Check the Hermes version and the read access of STETHO |
| No gateway to check | Check the list of profiles in the settings |
| Profiles that cannot be listed | Check the read access to the profiles folder |
| Folder name that cannot be a profile name | Check the names of the folders |
| `running` | None |

With `desired_state: stopped`, every advice becomes that of the deliberate stop, so that nobody
is told to start what the operator stopped. With an intent STETHO does not know, which could mean
a deliberate pause, an advice to start or restart becomes: check the Hermes version and whether
the stop was intended, then read the logs. Both are in the code, like the protection of actions.

- **Secondary profile.** Its advice names the profile.
- **Container restart.** It stops every gateway. An advice that speaks of it says so, for a
  secondary profile and whenever there are several gateways.

### Thresholds

An installation changes them in `stetho.yaml`, under `modules.gateway.overrides`:
`late_after_minutes` (2), `down_after_minutes` (5) and `missing_critical_after_minutes` (5).
They are whole numbers of minutes greater than 0, and the first must be lower than the second.
The configuration checks the names and values; `load_rules` checks the order and raises
`RulesError` when the sensor is created.

## Sensor

`sensor.py` reads every gateway, applies the rules and returns one
[result](probe.md#result-format) with the anomalies. Its group is "Availability".

- **One gateway.** The card holds its status, sentence, advice, details and actions.
- **Several.** One item per gateway, named after `hermes.profile_labels` or the profile. The
  card takes the worst status, and the sentence and advice of the first gateway that has it, the
  sentence with its name.
- **Actions** are offered for the main profile only: an action names no profile, so NURSE would
  act on the main one.
- **Unchecked.** If some profiles cannot be listed, or if there is no gateway to check, the card
  is at least `unknown`.
- **Details.** Last sign of life, stop reason, active agents, Hermes version and last unclean
  stop. The date of the latter is in the time zone of the installation. A reason that is not
  confirmed is not shown: the page points to the Hermes logs.

### Memory

The sensor keeps two things **until STETHO restarts**:

- since when a state file is missing: after a restart, the 5 minutes start again;
- with `profiles: auto`, the profiles already seen. A profile whose file disappears stays
  expected, unless the main gateway now serves it. A profile removed on purpose is therefore
  reported until the restart.

**Limit.** A command writes the date again. Repeated commands on a dead gateway can therefore
postpone `critical`.

## Tests

`tests/stetho/test_gateway_reader.py` and `test_gateway_sensor.py` use the made-up files in
`tests/fixtures/hermes-0.21/`, with their reference time; the second goes from the files to the
result. `test_gateway_rules.py` covers each rule, the thresholds and the protections.

## Decisions

- **Missing state file.** `unknown`, then `critical` after 5 minutes.
- **Order.** Stopped cases are checked before the age, unlike in the initial specification.
- **Deliberate stop.** It stays `critical`, since Hermes does not work, and no repair is offered.
- **Date written by a command.** It is not a sign of life.
- **`starting` and `draining`.** `warn`, although a long normal start or stop can then lead to an
  alert.
- **Exit reason.** Only confirmed reasons are translated, since the text could hold a secret.
- **Profiles.** One sensor, with one item per gateway when there are several.
