-- Migration 003: create alert_rules table (unified rule configuration)
-- Replaces: alert_settings (key-value) for threshold config
-- Complements: alert_cooldowns (runtime cooldown tracking) and alert_history (trigger log)

CREATE TABLE IF NOT EXISTS alert_rules (
    rule_name        VARCHAR(100) PRIMARY KEY,
    enabled          BOOLEAN NOT NULL DEFAULT TRUE,
    threshold        DOUBLE PRECISION,
    cooldown_seconds INTEGER NOT NULL DEFAULT 3600,
    severity         VARCHAR(20) NOT NULL DEFAULT 'MEDIUM'
                     CHECK (severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    description      VARCHAR(255),
    updated_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_alert_rules_enabled ON alert_rules (enabled);
CREATE INDEX IF NOT EXISTS ix_alert_rules_severity ON alert_rules (severity);
