-- Made-up test data for Hermes v0.21.4 (tag v2026.9.21).
-- Table definitions come from cron/executions.py of Hermes Agent,
-- Copyright (c) 2025 Nous Research, MIT License. Rows are written by hand.
-- Indexes, triggers, search tables and tables that are not read are left out.

CREATE TABLE executions (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL,
  source TEXT NOT NULL,
  process_id TEXT NOT NULL,
  pid INTEGER NOT NULL,
  process_started_at INTEGER,
  status TEXT NOT NULL CHECK(status IN
    ('claimed','running','completed','failed','unknown')),
  handoff_pending INTEGER NOT NULL DEFAULT 0,
  handoff_started_at REAL,
  claimed_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  error TEXT
);

ALTER TABLE executions ADD COLUMN delivery_outcome TEXT;

ALTER TABLE executions ADD COLUMN scheduled_instant TEXT;
