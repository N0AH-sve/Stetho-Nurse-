-- Made-up rows, written by hand.
INSERT INTO tasks (id, title, status, created_at, started_at, completed_at) VALUES ('t_00000001', 'Sample finished task', 'done', 1768471200, 1768471300, 1768474800);
INSERT INTO tasks (id, title, assignee, status, created_at, started_at, last_heartbeat_at, max_runtime_seconds, current_run_id) VALUES ('t_00000002', 'Sample running task', 'worker', 'running', 1768477500, 1768477800, 1768478340, 3600, 2);
INSERT INTO task_runs (id, task_id, status, started_at, ended_at, outcome) VALUES (1, 't_00000001', 'done', 1768471300, 1768474800, 'completed');
INSERT INTO task_runs (id, task_id, status, started_at, last_heartbeat_at) VALUES (2, 't_00000002', 'running', 1768477800, 1768478340);
