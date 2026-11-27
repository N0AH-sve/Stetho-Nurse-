# Configuration

> Status: STETHO settings are loaded and checked. The rest is to be written.

## Purpose

How an installation is described without changing the code.

## STETHO settings

[`stetho.example.yaml`](../../config/stetho.example.yaml) lists every setting, with comments.
`stetho/config.py` reads the file and checks all of it before anything else starts. Nothing
calls it yet: the STETHO program does not exist.

- **Required.** Every setting of the example must be present. Only `language` may be left out
  (`en`). No other value is assumed.
- **Empty secrets.** `auth.password` and `alerts.discord.webhook_url` must be present but may be
  empty. An empty password means that the reverse proxy handles authentication.
- **Numbers.** Ports go from 1 to 65535; durations and counts are whole numbers greater than 0.
- **Paths.** Directories are absolute paths. Whether they exist is not checked at startup: a
  missing source is reported while STETHO runs.
- **Names.** `hermes.profiles` is `auto` or a list, and `modules.kanban.boards` is `all` or a
  list. A list is not empty and has no repeat. Profile and board names hold up to 64 letters,
  digits, `_` and `-`, and start with a letter or a digit. Profile names follow the same rule in
  `profile_labels`.
- **Modules.** All seven modules are listed, each with `enabled`.
- **Alert channel.** Only `discord` is accepted for now.

## Errors at startup

An invalid file stops STETHO: `load_config` raises `ConfigError`. Only the first problem found
is reported.

| Problem | Message |
| --- | --- |
| File that cannot be read, over 256 KiB or not UTF-8 | The reason |
| Invalid YAML or repeated key | The line and column when known |
| Missing setting | The setting name |
| Unknown setting | The setting name; it is hidden if it looks like a pasted value |
| Wrong value | The setting name and what is expected |

A message never quotes a value or a line of the file, because it could be a secret. This also
applies to YAML syntax errors, whose original text copies the faulty line. Secrets are hidden
when the loaded settings are printed. The file is read with a safe YAML loader, which cannot
build Python objects.

## Tests

`tests/stetho/test_config.py` loads the example file, then covers each kind of error and checks
that messages do not repeat the content of the file.

## Planned content

- NURSE settings
- Rule overrides
- Action catalogue (`actions.toml`, see
  [technology choices](../tech-stack.md#4-format-of-the-nurse-action-catalogue))

## Decisions

- **Files only.** Neither service uses environment variables. STETHO reads
  [`stetho.yaml`](../../config/stetho.example.yaml) and NURSE reads
  [`nurse.toml`](../../config/nurse.example.toml). The local copies, without `.example`, are not
  committed. Changes apply after a restart.
- **Thresholds** are set in YAML.
- **Strict validation.** STETHO refuses to start on a missing, unknown or invalid setting, so
  that a typing mistake cannot go unnoticed.
- **Overrides** come with each module. Until a module supports them, `overrides` is refused.
- **Rate limits** are configurable for each action.
- **Language** of the page and the alerts is chosen at installation: French, English or
  Spanish. English is the default. No
  user-facing text is hard-coded, including rule messages, remediation steps, anomaly texts and
  action labels.
- **Translations.** One YAML file per language.
- **Action labels.** The catalogue holds one label per language; English is the fallback.
