# Session database

Reference version and conventions: [Hermes data](README.md). File: `state.db`, one per profile.
Source of the schema: `hermes_state_common.py`, schema version **30**, stored in the table
`schema_version`.

## Tables used

| Table | Use |
| --- | --- |
| `sessions` | One row per session |
| `messages` | Size computation only |
| `session_model_usage` | Tokens and cost per model |
| `schema_version` | Format detection |

In these tables, `started_at`, `ended_at`, `last_activity_at`, `timestamp`, `first_seen` and
`last_seen` are epoch seconds with decimals.

Useful columns of `sessions`: `id`, `source`, `user_id`, `display_name`, `chat_id`,
`chat_type`, `thread_id`, `profile_name`, `cwd`, `git_branch`, `title`, `model`,
`parent_session_id`, `started_at`, `last_activity_at`, `ended_at`, `end_reason`,
`message_count`, `tool_call_count`, `input_tokens`, `output_tokens`, `cache_read_tokens`,
`reasoning_tokens`, `estimated_cost_usd`, `actual_cost_usd`, `compression_failure_error`,
`handoff_error`.

## Content that must not be read

`state.db` holds conversations. Do not select the values of `messages.content`, `tool_calls`,
`reasoning`, `reasoning_content`, `reasoning_details`, `codex_reasoning_items`,
`codex_message_items`, `api_content`, of `sessions.system_prompt`, `origin_json`,
`model_config`, or of the table `system_prompts`. The full-text search tables also hold message
text.

To measure a size, use an aggregate such as `sum(length(CAST(content AS BLOB)))`: `length` on
text counts characters, not bytes. SQLite still reads the content to compute it.

## Size and migrations

- The database may be large: limit and page every query, and keep reads short.
- Hermes adds columns when it upgrades. Check the columns before use.
