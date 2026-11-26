-- Made-up test data for Hermes v0.21.4 (tag v2026.9.21).
-- Table definitions come from cron/incidents.py of Hermes Agent,
-- Copyright (c) 2025 Nous Research, MIT License. Rows are written by hand.
-- Indexes, triggers, search tables and tables that are not read are left out.

CREATE TABLE cron_incidents (
  id            TEXT PRIMARY KEY,
  job_id        TEXT NOT NULL,
  error_sig     TEXT NOT NULL,
  state         TEXT NOT NULL,
  failure_type  TEXT NOT NULL DEFAULT 'unknown',
  first_seen_at TEXT NOT NULL,
  last_seen_at  TEXT NOT NULL,
  acked_at      TEXT,
  alerted_at    TEXT,
  closed_at     TEXT,
  error         TEXT NOT NULL,
  output_file   TEXT
);
