# Sessions

> Status: to be written.

## Purpose

Lists the sessions of each profile with their size and cost, without any message content.

## Planned content

- Displayed fields, filters and sorting
- Rules and thresholds
- Incremental size computation
- Privacy and identity masking

## Decisions

- **Database size rule.** It appears both here and in [weaknesses](weaknesses.md). Each
  module sends its own alert, with a warning that both concern the same database.

## Open points

- The specification gives the sessions version of this rule the `info` level, which never alerts.
  Its level must be raised for this module to send its alert.
