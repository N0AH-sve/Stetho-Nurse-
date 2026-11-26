-- Made-up rows, written by hand.
INSERT INTO schema_version (version) VALUES (30);
INSERT INTO sessions (id, source, started_at, last_activity_at, message_count, input_tokens, estimated_cost_usd, compression_failure_error) VALUES ('session-big', 'discord', 1768392400.0, 1768478340.0, 900, 5000000, 42.5, 'Made-up compression error');
INSERT INTO sessions (id, source, parent_session_id, started_at, ended_at, end_reason, handoff_error) VALUES ('session-child', 'cron', 'session-big', 1768473400.0, 1768474400.0, 'sample_end', 'Made-up handoff error');
