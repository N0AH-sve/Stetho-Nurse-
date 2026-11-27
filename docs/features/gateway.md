# Gateway

> Status: the reader is implemented. Rules, texts and the sensor are to be written.

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

`stetho/modules/gateway/reader.py` turns the files into neutral data. Nothing calls it yet.

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

## Tests

`tests/stetho/test_gateway_reader.py` uses the made-up files in
`tests/fixtures/hermes-0.21/`, with their reference time.

## Planned content

- Rules and thresholds
- Texts, sensor result and suggested actions

## Decisions

- **Missing state file.** `unknown`, then `critical` after 5 minutes.

The following rules are decided and not implemented yet. They replace those of the initial
specification where they differ.

- **Order.** `stopped` and `startup_failed` are checked before the age of the date, so that a
  stopped gateway is not announced as not answering. They are `critical`. So is `degraded` with
  a watchdog reason, because the process is dead. `RESTART_GATEWAY` is offered for these three.
- **Deliberate stop.** `stopped` with `desired_state: stopped` stays `critical`, says that the
  stop is deliberate and offers **no action**. Elsewhere, `RESTART_GATEWAY` is offered as
  planned, although it may do nothing on a stopped gateway: to be settled with NURSE.
- **Date written by a command.** Outside the stopped cases above: `unknown` up to 2 minutes,
  then `warn`, then `critical` after 5 minutes, as long as no real sign of life comes back.
- **`starting` and `draining`** with a recent sign of life are `warn`.
- **Exit reason.** Only the confirmed reasons are translated. For any other, the page says that
  the reason is not shown and points to the Hermes logs.
- **Profiles.** One sensor, with one item per gateway when there are several.
