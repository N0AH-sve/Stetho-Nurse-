# Errors and anomalies

> Status: the anomaly codes, their model, grouping, texts, reading retries and end are
> implemented, as well as the log of the [probe](probe.md#probe). So far, only the
> [`gateway` module](gateway.md) and the probe raise anomalies. Anomalies are not served yet.

## Purpose

Reports everything that does not match what is expected, from data formats to action output.
STETHO never hides an error and never shows `ok` when it could not check.

## Codes

"Expected behaviour" is what the part that raises the anomaly has to do. **So far, the reading
retries, the effects on the [`gateway` rules](gateway.md#rules) and those of the sensor codes
are implemented. The effects on actions are not.**

| Code | When | Expected behaviour | What to do |
| --- | --- | --- | --- |
| `source_missing` | An expected file or database does not exist | Sensor `unknown`, then `critical` after 5 minutes | Check that Hermes is running and that its data directory is mounted |
| `source_unreadable` | Access denied, database locked or other read error | 3 new attempts after 0.2 s, 0.5 s and 1 s, then `unknown` | Check the access rights of the data |
| `parse_error` | Broken JSON or corrupt database | One new reading after 1 s, then `unknown` | If it lasts, check the file |
| `unexpected_value` | A value outside the expected list | Value marked as unknown; sensor at least `warn` | Check the Hermes version; the adapter may need the new value |
| `schema_changed` | A field, table or column is missing, or a new one appears | Required one missing: `unknown`. New one: ignored and reported once as `info` | Check the Hermes version and update the adapter |
| `implausible_value` | An impossible value, such as a date more than 5 minutes in the future or a negative count | Value shown struck out and not used by the rules | Check the clock and the source |
| `sensor_error` | Internal error of a sensor | Sensor `unknown`; the others continue | Report it with the error type and place |
| `sensor_timeout` | A sensor is too slow | Stopped; `unknown` for this reading | If it lasts, check the size of the sources and the timeout |
| `action_failed` | A NURSE command ends with an unexpected exit code | Result `failed`, never retried automatically, alert | Read the result of the action |
| `action_timeout` | A command exceeds its timeout | Result `timeout`, alert | Check whether it is still running before trying again |
| `action_unexpected_output` | Good exit code, but unexpected output | Result `unexpected`, alert | Read the result of the action |
| `action_unverified` | The command succeeded, but its effect is not visible | Result `unverified`, alert, automatic actions suspended for 1 hour | Check the state in Hermes by hand |
| `proxy_denied` | The Docker proxy refuses a call from NURSE | Result `refused`, `critical`, alert at once | Check now: the configuration is wrong or an unlisted call was tried |

## Reading retries

`stetho/sources.py` reads a file without ever modifying it. An unreadable file is read again
after 0.2 s, 0.5 s and 1 s; invalid content is read again once after 1 s. A missing file is
reported at once. These waits count towards the time limit of a sensor, and the probe
[limits their total](probe.md#isolation-and-time-limit).

## Model

Defined in `stetho/anomalies.py`. Every anomaly has a `code` and a `module`. It takes only the
fields its code needs: a missing or extra one raises `ValueError`.

| Codes | Fields |
| --- | --- |
| `source_missing`, `parse_error` | `source` |
| `source_unreadable` | `source`, `reason`: `permission_denied`, `locked` or `other` |
| `unexpected_value` | `source`, `field`, `received`, `expected` |
| `schema_changed` | `source`, `field` |
| `implausible_value` | `source`, `field`, `received` |
| `sensor_error` | `error`: see [internal errors](#internal-errors) |
| `sensor_timeout` | None |
| `action_failed` | `action`, `exit_code`, optional `target` |
| `action_timeout` | `action`, `timeout_seconds`, optional `target` |
| Other action codes | `action`, optional `target` |

`source` is a relative path inside the Hermes data directory.

A sensor result lists the **codes** of its anomalies in `anomalies`, without repeats. See the
[result format](probe.md#result-format).

## What is kept from outside

No secret and no message content may reach the page, the errors or the logs.

- **Received value.** Kept for `unexpected_value` and `implausible_value` only. A number, a
  boolean, an empty JSON value (`null`) or a text of up to 64 letters, digits, `_`, `-`, `.`,
  `:` and `+` is kept. Anything else is shown as "hidden value". This replaces the cut at 200
  characters of the initial specification.
- **Limit.** A short token that has this form would be kept. A reader must therefore raise these
  two codes only for fields that are not meant to hold a secret or message content. `field`,
  `source` and `expected` are not filtered: `expected` comes from STETHO's own files, and a
  reader must not build the other two from content.
- **Other values.** There is no field for a raw message or a command output. Error messages of
  the model quote no value.

## Internal errors

`stetho/errors.py` summarises an exception as its **type** and the **calls in STETHO's code**:
file, line and function.

- The exception message is never read: it could copy data from Hermes. There is no debug mode
  that shows it.
- Calls outside the STETHO package are left out, so no path of the installation appears. Chained
  exceptions are not followed.
- The short form, for the detail of the anomaly, holds the type and the closest call, such as
  `KeyError · stetho/results.py:42 (worst_status)`. The whole list of calls is meant for the log.

This may not be enough to find the cause. The problem must then be reproduced with made-up data.

## Texts

`describe` returns the sentence of an anomaly in the language of the installation, from the
[translation files](configuration.md#translations). Action sentences have a second form that
names the target. The initial specification planned an `anomalies.yaml` file and one page per
code: the texts are in the translation files instead, and this page covers all the codes.

## Grouping

`AnomalyRegister` groups the anomalies that have the same module, source, code, field, action
and target. Action and target keep apart the failures of two different actions. An entry has a
stable identifier, the latest occurrence, the first and last time seen, and a count.

The register is in memory. The [probe](probe.md#anomalies-and-log) removes the anomalies that
are over. No history is kept.

## Tests

`tests/stetho/test_anomalies.py` covers the fields of each code, the filter of received values,
the sentences in the three languages and the grouping. `tests/stetho/test_errors.py` checks that
a summary holds no message and no outside path.

## Planned content

- Effects on statuses for the other modules, with their rules
- History of the ended anomalies for 7 days, and saving
- Alerts: when an anomaly lasts for 2 checks, at most once an hour for the same one;
  `proxy_denied` at once
