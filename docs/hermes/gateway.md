# Gateway files

Reference version and conventions: [Hermes data](README.md). Paths are relative to the Hermes
home of a profile.

## `gateway_state.json`

Compact JSON, replaced atomically. Source: `gateway/status.py`.

| Field | Type | Notes |
| --- | --- | --- |
| `gateway_state` | string | See below |
| `updated_at` | ISO 8601 string, UTC, or integer | Re-stamped every 60 s by the housekeeping thread. `gateway start`, `stop` and `restart` write epoch seconds, [which is not a heartbeat](commands.md#the-cli-makes-the-state-file-look-alive) |
| `exit_reason` | string or null | |
| `restart_requested` | boolean | |
| `active_agents` | integer | |
| `platforms` | object | One entry per platform, see below |
| `session_store` | object | `status`: `ok`, `unavailable`, `retrying` or `unknown` |
| `pid`, `start_time` | integer | `start_time` is a process fingerprint, not a date |
| `kind` | string | `hermes-gateway` |
| `argv` | list of strings | May hold local paths |
| `hermes_home` | string | Local path |
| `code_sha`, `code_version` | string or null | May be absent |
| `active_work` | list or null | Set while draining |
| `served_profiles` | list | One gateway serving several profiles |
| `multiplex_standalone_reason` | string or null | |
| `desired_state` | string | Operator intent (`running` or `stopped`), written by the CLI and the container boot; may be absent |

Hermes itself treats a snapshot as stale after 120 s.

### Values of `gateway_state`

| Value | Meaning |
| --- | --- |
| `starting` | Starting up |
| `running` | Serving |
| `degraded` | Serving with platforms waiting for a retry, or stopped by an internal watchdog |
| `draining` | Finishing current work before a stop or while going dormant |
| `stopped` | Stopped |
| `startup_failed` | Startup failed |

- A watchdog stop writes `degraded` with `exit_reason` set to `loop_liveness_watchdog` or
  `shutdown_watchdog`, then exits. The process is dead although the state is not `stopped`.
- After an **unexpected signal**, the gateway deliberately writes `running` on exit so that it
  restarts with the container. `running` alone does not prove it is alive: check `updated_at`.
- When the container is first created, the file may contain only
  `{"gateway_state":"running"}`.

### `platforms`

The key is the platform name, such as `discord`, or `<profile>:<platform>` when one gateway
serves several profiles.

| Field | Notes |
| --- | --- |
| `state` | `connecting`, `connected`, `retrying`, `fatal`, `paused`, `disconnected`, `disabled` |
| `error_code`, `error_message` | String or null. `error_message` is free text |
| `needs_attention` | Boolean, may be absent |
| `retrying_since` | ISO 8601 string or null, may be absent |
| `updated_at` | Date of the last change of this entry, not a heartbeat |
| `writer_pid`, `writer_start_time` | Process that wrote the entry |

Entries are merged over time: an entry may belong to an earlier gateway process.

## `state/gateway.heartbeat`

JSON rewritten every 30 s by the gateway event loop. Source: `gateway/shutdown_watchdog.py`.

Fields: `pid`, `updated_at`, `monotonic`, `start_time` (optional, epoch seconds), `mem`
(optional, in KiB: `rss_kib`, `mem_total_kib`, `mem_available_kib`, `swap_used_kib`),
`loop_tick_socket` and `loop_tick_tcp_port`.

Hermes does not rely on this file alone to declare the loop frozen: it also probes a socket.

## `state/gateway.lifecycle.json`

JSON describing the current or last gateway life. Source: `gateway/lifecycle_ledger.py`.

- Running: `phase: "running"`, `pid`, `start_time`, `started_at`, and sometimes `create_time`,
  `prior_unclean_exit`, `prior_suspected_oom`.
- Exited: `phase: "exited"`, `pid`, `exit_code`, `exit_reason`, `exited_at`.

`phase: "running"` is normal while the gateway runs. It means an unclean stop only if that
process is dead, which STETHO cannot check from its own container.

## `logs/gateway-exit-diag.log`

One JSON object per line, appended. Common fields: `ts`, `tag`, `pid`. The event name is in
`tag`. Sources: `hermes_cli/gateway.py` and `gateway/lifecycle_ledger.py`.

| `tag` | Meaning |
| --- | --- |
| `gateway.start` | A gateway starts |
| `gateway.previous_unclean_exit` | The previous life ran no exit path |
| `gateway.exit_clean`, `gateway.exit_nonzero` | End of the run |
| `atexit.hook`, `asyncio.run.returned`, `asyncio.run.KeyboardInterrupt`, `asyncio.run.SystemExit`, `asyncio.run.exception` | Exit diagnostics |

`gateway.previous_unclean_exit` may add `prior_pid`, `prior_started_at`, `prior_start_time`,
`last_heartbeat_at`, `last_heartbeat_mem`, `suspected_oom` and `state_db_integrity`. It holds
no field naming the cause.

`argv` and `traceback` fields may hold local paths or sensitive text.

## `.clean_shutdown`

Empty file created at the end of a clean stop, unless the drain timed out. The next startup
**deletes it**. Its absence while the gateway runs is normal and says nothing about the last
stop.
