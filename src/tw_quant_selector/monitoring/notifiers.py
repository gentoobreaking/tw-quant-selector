"""Notification channels and alert message formatting.

Pure transmission logic — no DB dependency (except ``get_alert_config``
which reads settings from DB/env).

Module: notifiers.py
"""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from typing import Any, Optional

import httpx
import structlog

log = structlog.get_logger()


def format_alert(severity: str, rule: str, message: str, suggestion: Optional[str] = None, **context) -> str:
    icons = {
        "CRITICAL": "🚨",
        "HIGH": "⚠️",
        "MEDIUM": "📌",
        "LOW": "📊"
    }
    icon = icons.get(severity, "🔔")
    msg = f"{icon} {severity} / {rule}\n{message}"

    if suggestion:
        msg += f"\n\n💡 建議行動: {suggestion}"

    if context:
        msg += "\n\nContext:"
        for k, v in context.items():
            msg += f"\n- {k}: {v}"
    return msg


def get_alert_config(db) -> dict[str, Any]:
    # Keys we support
    keys = [
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
        "SMTP_SERVER", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD",
        "EMAIL_SENDER", "EMAIL_RECIPIENT",
        "PL_THRESHOLD", "PL_PERCENT_THRESHOLD"
    ]

    db_settings = {r[0]: r[1] for r in db.execute("SELECT key, value FROM alert_settings").fetchall()}
    config = {}
    for k in keys:
        val = os.getenv(k) or db_settings.get(k)
        config[k] = val
    return config


class TelegramNotifier:
    def __init__(self, token: Optional[str] = None, chat_id: Optional[str] = None):
        self.token = token
        self.chat_id = chat_id

    def send(self, message: str):
        if not self.token or not self.chat_id:
            log.warning("alert.telegram.missing_config")
            return
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:
            resp = httpx.post(url, json={"chat_id": self.chat_id, "text": message}, timeout=10.0)
            resp.raise_for_status()
            log.info("alert.telegram.sent")
        except Exception as e:
            log.error("alert.telegram.failed", error=str(e))


class EmailNotifier:
    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}

    def send(self, subject: str, body: str):
        c = self.config
        server_addr = c.get("SMTP_SERVER")
        port = int(c.get("SMTP_PORT") or 587)
        user = c.get("SMTP_USER")
        pwd = c.get("SMTP_PASSWORD")
        sender = c.get("EMAIL_SENDER") or user
        recipient = c.get("EMAIL_RECIPIENT")

        if not all([server_addr, user, pwd, recipient]):
            log.warning("alert.email.missing_config")
            return

        msg = EmailMessage()
        msg.set_content(body)
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = recipient

        try:
            with smtplib.SMTP(server_addr, port, timeout=15) as server:
                server.starttls()
                server.login(user, pwd)
                server.send_message(msg)
            log.info("alert.email.sent", to=recipient)
        except Exception as e:
            log.error("alert.email.failed", error=str(e))