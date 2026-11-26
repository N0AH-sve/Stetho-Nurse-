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
- **Level.** In this module the rule is a `warn`, so that it sends its alert.
