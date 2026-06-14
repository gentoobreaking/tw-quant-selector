-- Migration 002: add resolved_at + resolution_note to alert_history
-- Fixes: GET /api/v1/alerts/history returning 500 due to missing columns

ALTER TABLE alert_history
    ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMP NULL,
    ADD COLUMN IF NOT EXISTS resolution_note TEXT NULL;

CREATE INDEX IF NOT EXISTS ix_alert_history_resolved_at
    ON alert_history (resolved_at);
