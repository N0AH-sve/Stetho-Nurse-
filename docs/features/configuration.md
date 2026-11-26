# Configuration

> Status: to be written.

## Purpose

How an installation is described without changing the code.

## Planned content

- NURSE settings
- STETHO settings and rule overrides
- Action catalogue (`actions.toml`, see
  [technology choices](../tech-stack.md#4-format-of-the-nurse-action-catalogue))
- Validation at startup

## Decisions

- **Files only.** Neither service uses environment variables. STETHO reads
  [`stetho.yaml`](../../config/stetho.example.yaml) and NURSE reads
  [`nurse.toml`](../../config/nurse.example.toml). The local copies, without `.example`, are not
  committed. Changes apply after a restart.
- **Thresholds** are set in YAML.
- **Rate limits** are configurable for each action.
- **Language** of the page and the alerts is chosen at installation: French, English or
  Spanish. English is the default. No
  user-facing text is hard-coded, including rule messages, remediation steps, anomaly texts and
  action labels.
- **Translations.** One YAML file per language.
- **Action labels.** The catalogue holds one label per language; English is the fallback.
