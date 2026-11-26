# Probe and modules

> Status: to be written.

## Purpose

Runs the sensors periodically and gives them a common result format.

## Planned content

- Result format and statuses
- Scheduling, isolation and timeouts
- Module layout
- Hermes version detection and adapters

## Decisions

- **Layout.** One folder per domain in `modules/`; there is no separate `sensors/` folder.
- **Crashing sensor.** It reports `unknown`.
- **`details` format.** A list of label and value pairs.
