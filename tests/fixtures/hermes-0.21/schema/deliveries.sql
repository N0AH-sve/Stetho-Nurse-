-- Made-up test data for Hermes v0.21.4 (tag v2026.9.21).
-- Table definitions come from cron/delivery_queue.py of Hermes Agent,
-- Copyright (c) 2025 Nous Research, MIT License. Rows are written by hand.
-- Indexes, triggers, search tables and tables that are not read are left out.

CREATE TABLE deliveries (
  execution_id TEXT PRIMARY KEY,
  job_json TEXT NOT NULL,
  content TEXT NOT NULL,
  for_failure INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL CHECK(status IN
    ('pending','delivering','delivered','failed','unknown','suppressed')),
  owner_process_id TEXT,
  owner_pid INTEGER,
  owner_started_at INTEGER,
  created_at TEXT NOT NULL,
  finished_at TEXT,
  error TEXT
);
