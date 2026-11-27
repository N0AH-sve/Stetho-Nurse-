# Probe and modules

> Status: statuses and the result format are implemented. The rest is to be written.

## Purpose

Runs the sensors periodically and gives them a common result format.

## Statuses

| Status | Meaning |
| --- | --- |
| `ok` | Checked and healthy |
| `warn` | A problem to look at |
| `critical` | A serious problem |
| `info` | Shown only: never a problem and never an alert |
| `unknown` | STETHO could not check: never shown as healthy |

From the most to the least severe: `critical`, `warn`, `unknown`, `ok`. `info` has no severity.

The **overall status** is the most severe one among the sensors, ignoring `info`. If nothing is
left, because there is no result or only `info` ones, it is `unknown`.

## Result format

Defined in `stetho/results.py`.

| Field of a sensor | Content |
| --- | --- |
| `id` | Up to 64 lowercase letters, digits and `_`, starting with a letter |
| `title`, `group`, `summary` | Texts, not empty |
| `status` | One of the statuses |
| `checked_at` | Time of the reading, with a time zone |
| `details` | List of label and value pairs; only the value may be empty |
| `items` | Elements followed by the sensor, such as one per scheduled job |
| `actions` | Action codes offered as buttons |
| `anomalies` | Codes of the current [anomalies](anomalies.md), without repeats |

An **item** has an `id`, a `label`, a `status`, a `summary`, `details` and `actions`. It has no
sub-items. Its `id` is not empty and is unique within its sensor.

- **Texts** are already in the language of the installation.
- **Actions.** A code uses up to 64 uppercase letters, digits and `_`, starting with a letter,
  and is not repeated in a list. Only the form is checked here: NURSE decides which codes exist.
- **Items and their sensor.** When an item is `unknown`, `warn` or `critical`, the sensor is at
  least as severe, and cannot be `info`. A green card therefore cannot hide a failing item. The
  sensor may be more severe than its items, for example when its source has a problem. Items
  that are `ok` or `info` set no limit.
- **Invalid result.** It cannot be built: `ValueError` is raised, without quoting any value.

## Tests

`tests/stetho/test_results.py` covers the order of the statuses and each rule of the format.

## Planned content

- Scheduling, isolation and timeouts
- Module layout
- Hermes version detection and adapters

## Decisions

- **Layout.** One folder per domain in `stetho/modules/`; there is no separate `sensors/` folder.
  So far, [`gateway`](gateway.md) has its reader, adapter, rules and sensor.
- **Crashing sensor.** It reports `unknown`.
- **`details` format.** A list of label and value pairs.
