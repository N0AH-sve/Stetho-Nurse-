# Known weaknesses

> Status: to be written.

## Purpose

Watches the known ways Hermes can degrade silently, such as disk, memory and growing files.

## Planned content

- Measurement types
- One section per check: risk, thresholds, what to do
- Adding a check

## Decisions

- **Database size rule.** It appears both here and in [sessions](sessions.md). Each
  module sends its own alert, with a warning that both concern the same database.
