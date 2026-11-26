# Cron files

Reference version and conventions: [Hermes data](README.md). Each profile has its own `cron/`
folder.

## Ticker markers

Text files replaced atomically. Source: `cron/jobs.py`.

| File | Content |
| --- | --- |
| `cron/ticker_heartbeat` | Epoch seconds with decimals, written at each loop (every 60 s) |
| `cron/ticker_last_success` | Same, only after a pass without error |
| `cron/ticker_last_error` | Epoch seconds on the first line, then the message. Removed after a successful pass |
| `cron/catch_up_occurrences` | Integer counter |

## `cron/jobs.json`

Indented JSON: `{"jobs": [...], "updated_at": "<ISO 8601>"}`. Dates are ISO 8601 with an offset.
Hermes also accepts older or hand-edited shapes, such as a bare list, and repairs them.

Fields useful to monitoring:

| Field | Notes |
| --- | --- |
| `id` | 12 hexadecimal characters when Hermes creates it |
| `name` | May be derived from the prompt: treat it as possible message content |
| `schedule` | `{"kind": "cron", "expr"}`, `{"kind": "interval", "minutes"}` or `{"kind": "once", "run_at"}`, with `display` |
| `schedule_display` | String |
| `enabled` | Boolean, authoritative over `state` |
| `state` | `scheduled`, `paused`, `completed`, `error` |
| `next_run_at`, `last_run_at` | Date or null |
| `last_status` | null, `ok`, `error`, `delivery_failed`, `delivery_queued`, `blocked_config` |
| `last_error`, `last_delivery_error` | Free text or null |
| `failure_streak` | Consecutive failed runs; delivery failures do not count |
| `run_claim` | `{"at", "by"}`, null or absent |
| `fire_claim` | `{"at", "by"}` or null |
| `paused_at`, `paused_reason` | Set on pause |
| `last_fire_error` | `{"at", "detail"}`, may be absent |

`state: "error"` means that Hermes could not compute the next run of a recurring job.

A job also holds `prompt`, `script`, `origin`, `base_url` and other settings. STETHO must not
keep or show them.

## `cron/executions.db`

### Table `executions`

Source: `cron/executions.py`. `status`: `claimed`, `running`, `completed`, `failed`,
`unknown`. Dates are ISO 8601 text. `delivery_outcome`: `delivered`, `queued`, `failed`,
`not_configured`, `suppressed`, `suppressed_acked` or null. Hermes keeps the last 1000 finished
rows.

### Table `cron_incidents`

Source: `cron/incidents.py`.

| `state` | Meaning |
| --- | --- |
| `detected` | Recorded |
| `alerted` | A failure message reached the operator; see `alerted_at` |
| `resolved` | The job succeeded again |
| `closed` | Silenced by the operator |

A resolved incident that repeats goes back to `detected`, and its `alerted_at` is cleared.
`failure_type`: `rate_limit`, `timeout`, `auth`, `delivery`, `config`, `script`, `agent`,
`unknown`. `error` is cut at 500 characters; Hermes tries to redact secrets in it.

The table is created on first use and may be absent.

## `cron/deliveries.db`

Table `deliveries`, source `cron/delivery_queue.py`. `status`: `pending`, `delivering`,
`delivered`, `failed`, `unknown`, `suppressed`. Dates are ISO 8601 text.

**The columns `content` and `job_json` hold message content and job prompts.** Never select
them, and never use `SELECT *`.
