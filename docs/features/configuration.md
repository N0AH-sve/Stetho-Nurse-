# Configuration

> Status: to be written.

## Purpose

How an installation is described without changing the code.

## Planned content

- Environment variables
- STETHO settings and rule overrides
- Action catalogue (`actions.toml`, see
  [technology choices](../tech-stack.md#4-format-of-the-nurse-action-catalogue))
- Validation at startup

## Decisions

- **Thresholds** are set in YAML. Environment variables hold installation values and switches.
- **Rate limits** are configurable for each action.
- **Language** of the page and the alerts is chosen at installation: French, English or
  Spanish. English is the default. No
  user-facing text is hard-coded, including rule messages, remediation steps, anomaly texts and
  action labels.

## Open points

- Format of the translation files, and language of the texts defined in the action catalogue.
