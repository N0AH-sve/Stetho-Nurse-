-- Made-up rows, written by hand.
INSERT INTO schema_version (version) VALUES (30);
INSERT INTO sessions (id, source, user_id, display_name, chat_id, chat_type, title, model, started_at, last_activity_at, message_count, tool_call_count, input_tokens, output_tokens, estimated_cost_usd)
VALUES ('session-1', 'discord', 'user-1', 'Test User', 'chat-1', 'channel', 'Sample session', 'sample-model', 1768474800.0, 1768478280.0, 2, 0, 1200, 300, 0.05);
INSERT INTO sessions (id, source, started_at, ended_at, end_reason) VALUES ('session-2', 'cli', 1768388400.0, 1768389400.0, 'sample_end');
INSERT INTO messages (session_id, role, content, timestamp) VALUES ('session-1', 'user', 'MADE-UP CONTENT: must never be shown', 1768474900.0);
INSERT INTO messages (session_id, role, content, timestamp) VALUES ('session-1', 'assistant', 'MADE-UP ACCENTS: éàü', 1768475000.0);
INSERT INTO session_model_usage (session_id, model, input_tokens, output_tokens, estimated_cost_usd) VALUES ('session-1', 'sample-model', 1200, 300, 0.05);
