# Configuration

> Status: STETHO settings and translations are loaded and checked. The rest is to be written.

## Purpose

How an installation is described without changing the code.

## STETHO settings

[`stetho.example.yaml`](../../config/stetho.example.yaml) lists every setting, with comments.
`stetho/config.py` reads the file and checks all of it before anything else starts.

STETHO is started with the path of this file, which has no default:

```bash
uv run python -m stetho --config config/stetho.yaml
```

The log goes to the error output. STETHO does not start, with exit code 2, when the settings, the
translations or the rules are invalid, when it cannot listen on the host and port, or after any
other error found before serving.

| End | Exit code |
| --- | --- |
| `SIGINT` or `SIGTERM`, at the startup or while serving, then a normal stop | 0 |
| The web server cannot run, or the startup or shutdown of STETHO fails | 1 |
| STETHO does not start (see above) | 2 |

A stop waits at most 5 seconds for a reading in progress, whatever the sensor time limit: a
stuck sensor does not hold it. **Limit:** a signal received while Python is still loading the
program, before STETHO runs, is not handled this way.

In the failures, the log gives the error type and the calls in STETHO, never a message or a
traceback. Errors that nothing catches, in a thread for example, are written the same way. The
web server is given all its settings, so that it reads no environment variable.

- **Required.** Every setting of the example must be present. Only `language` (`en`) and the
  [gateway thresholds](gateway.md#thresholds) may be left out. No other value is assumed.
- **Empty secrets.** `auth.password` and `alerts.discord.webhook_url` must be present but may be
  empty. An empty password means that the reverse proxy handles authentication.
- **Password.** Empty, or at least 16 characters; characters are counted, not bytes.
- **Host names.** `server.allowed_hosts` is a non-empty list of distinct names or addresses,
  without port, wildcard or final dot; IPv6 addresses are between brackets. Case is ignored, and
  every written form of an IPv6 address is the same.
- **Characters.** No text may hold a NUL or half of a Unicode surrogate pair.
- **Numbers.** Ports go from 1 to 65535; durations and counts are whole numbers greater than 0.
- **Probe durations.** `probe.interval_seconds` and `probe.sensor_timeout_seconds` go from 1 to
  86400 seconds (24 hours). This is a guard rail against values that would break the probe, not
  an advised rhythm: the installation chooses it. At startup, the log says what the two settings
  give.
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

## Translations

The user-facing texts are in `stetho/translations/`: `en.yaml`, `fr.yaml` and `es.yaml`. They
ship with the code; an installation only chooses the language. `stetho/i18n.py` loads them.
For now they hold the names of the statuses, two banner texts (healthy, and nothing checked), the
[anomaly sentences](anomalies.md#texts), durations, the texts of the `gateway` module with its
advice, and those of the API.

- **Keys.** Texts can be grouped. A text is asked for by its dotted key, such as
  `banner.all_ok`. Each part of a key uses letters, digits and `_`, and does not start with a
  digit. YAML does not read `no`, `yes`, `on` or `off` as a text, so these are refused unless
  quoted.
- **Placeholders.** A plain name in braces, such as `{name}`, without format or attribute.
  `{{` and `}}` show a brace. An inserted value is never read as a template.
- **Same content.** All three files are checked together, whatever the chosen language. A
  missing or extra key, a different set of placeholders, an empty text or invalid YAML raises
  `TranslationError`, which names the file and the problem. There is therefore no fallback to
  another language.
- **Requests.** Asking for an unknown key, or without exactly the expected placeholders, raises
  `TranslationError`.

To add a text, add the same key to the three files. `tests/stetho/test_i18n.py` covers these
rules and checks the shipped files. Plural forms are not handled: durations use short units, such
as `5 min`, in `stetho/durations.py`. Dates are written as `2026-01-15 12:30`.

## Planned content

- NURSE settings
- Rule overrides of the other modules
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
  So far, only [`gateway`](gateway.md#thresholds) does.
- **Rate limits** are configurable for each action.
- **Language** of the page and the alerts is chosen at installation: French, English or
  Spanish. English is the default. No
  user-facing text is hard-coded, including rule messages, remediation steps, anomaly texts and
  action labels.
- **Translations.** One YAML file per language.
- **Action labels.** The catalogue holds one label per language; English is the fallback.
