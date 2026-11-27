# Probe and modules

> Status: statuses, the result format and the probe are implemented. No program starts the probe
> yet. Version adapters are to be completed.

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

## Probe

`stetho/probe.py` runs the sensors of the enabled modules. A pass returns a **report**: the
overall status, the time, one result per sensor in a fixed order, and the current anomalies. A
report does not change afterwards. `run_forever` makes a pass every `probe.interval_seconds`,
counted from the start of the previous one, until it is asked to stop. A pass that fails is
logged and does not end the monitoring.

### Isolation and time limit

- Sensors run at the same time, each in its own thread. They are waited for up to
  `probe.sensor_timeout_seconds`, in total and not one after the other.
- **Crash.** The sensor is `unknown`, with a `sensor_error` anomaly. Its card shows the
  [error type and place](anomalies.md#internal-errors), never the message.
- **Too slow.** The sensor is `unknown`, with a `sensor_timeout` anomaly. Python cannot stop a
  thread: the reading is left to finish and its result is ignored. The sensor is not started
  again until then. The threads do not keep STETHO from stopping.
- **Retries.** A reading may wait at most **half** of the time limit between its attempts. After
  that, attempts are made at once, so that the real cause is reported instead of a timeout. A
  sensor must wait only with the `sleep` it is given.

**Limit.** A sensor that stays blocked stays `unknown`: it never becomes `critical`.

### Anomalies and log

- After a **complete reading**, the anomalies of the sensor that were not seen again are over.
  Each one is counted once per reading.
- After a **crash or timeout**, nothing was checked: earlier anomalies stay. Only an earlier
  failure of the other kind ends.
- The log has a line when an anomaly **starts** and when it **ends**, not at each pass. It gives
  the module, code, source, field, action and target, never a received value. For an internal
  error, it adds the type and the calls in STETHO, again when they change.

## Module layout

Each module is a folder of `stetho/modules/`. [`gateway`](gateway.md) has a reader with its
adapters, rules and a sensor. A sensor has an `id`, a `title`, a `group` and
`read(now, sleep)`, which returns its result and anomalies. `build_sensors` lists the sensors of
the enabled modules.

## Tests

`tests/stetho/test_results.py` covers the order of the statuses and each rule of the format.
`tests/stetho/test_probe.py` covers isolation, the time limit, the retries, the end of
anomalies, the log, and runs the `gateway` module from its files to the report.

## Planned content

- Hermes version detection and the choice of an adapter, when a second version is supported

## Decisions

- **Layout.** One folder per domain in `stetho/modules/`; there is no separate `sensors/` folder.
- **Crashing sensor.** It reports `unknown`.
- **`details` format.** A list of label and value pairs.
- **Slow sensor.** It is not started again while its previous reading runs.
