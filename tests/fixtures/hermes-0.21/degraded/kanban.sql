-- Made-up rows, written by hand.
INSERT INTO tasks (id, title, assignee, status, created_at, started_at, last_heartbeat_at, max_runtime_seconds, current_run_id) VALUES ('t_000000a1', 'Sample silent task', 'worker', 'running', 1768471200, 1768471400, 1768476600, 3600, 1);
INSERT INTO tasks (id, title, status, created_at, consecutive_failures, last_failure_error) VALUES ('t_000000a2', 'Sample failing task', 'ready', 1768471200, 2, 'Made-up failure');
INSERT INTO tasks (id, title, status, created_at, block_kind) VALUES ('t_000000a3', 'Sample blocked task', 'blocked', 1768471200, 'needs_input');
INSERT INTO task_runs (id, task_id, status, started_at, last_heartbeat_at) VALUES (1, 't_000000a1', 'running', 1768471400, 1768476600);
INSERT INTO task_runs (task_id, status, started_at, ended_at, outcome, error) VALUES ('t_000000a2', 'crashed', 1768473400, 1768473500, 'crashed', 'Made-up failure');
INSERT INTO task_runs (task_id, status, started_at, ended_at, outcome, error) VALUES ('t_000000a2', 'timed_out', 1768474400, 1768475400, 'timed_out', 'Made-up failure');
