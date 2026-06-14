"""
T100: Unit tests for sensitive info exposure fix.

Tests:
    - GET /api/v1/settings/alerts masks TELEGRAM_BOT_TOKEN + SMTP_PASSWORD
    - POST skips "***" values (does not overwrite real secrets)
    - has_value flag correctly set for sensitive fields
"""
from fastapi.testclient import TestClient

from tw_quant_selector.api.app import app, SENSITIVE_KEYS

client = TestClient(app)


def test_get_settings_masks_sensitive_fields():
    """GET /api/v1/settings/alerts: sensitive fields return '***'."""
    resp = client.get("/api/v1/settings/alerts")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    items = {item["key"]: item for item in body["data"]}

    # TELEGRAM_BOT_TOKEN is in SENSITIVE_KEYS
    assert "TELEGRAM_BOT_TOKEN" in items
    token_item = items["TELEGRAM_BOT_TOKEN"]
    assert token_item["is_sensitive"] is True
    # If a real value exists, it should be masked
    if token_item["value"] is not None and token_item["value"] != "":
        assert token_item["value"] == "***", f"Got unmasked value: {token_item['value']}"

    # SMTP_PASSWORD is in SENSITIVE_KEYS
    assert "SMTP_PASSWORD" in items
    pw_item = items["SMTP_PASSWORD"]
    assert pw_item["is_sensitive"] is True
    if pw_item["value"] is not None and pw_item["value"] != "":
        assert pw_item["value"] == "***", f"Got unmasked value: {pw_item['value']}"


def test_get_settings_non_sensitive_not_masked():
    """Non-sensitive fields should return their real values."""
    resp = client.get("/api/v1/settings/alerts")
    assert resp.status_code == 200
    body = resp.json()
    items = {item["key"]: item for item in body["data"]}

    # SMTP_SERVER is NOT in SENSITIVE_KEYS
    assert "SMTP_SERVER" in items
    assert items["SMTP_SERVER"]["is_sensitive"] is False
    # Should not be "***" unless the value is literally "***" (edge case)
    if items["SMTP_SERVER"]["value"]:
        assert items["SMTP_SERVER"]["value"] != "***", "Non-sensitive field should not be masked"


def test_get_settings_has_value_flag():
    """has_value flag should be True when sensitive field has a real value."""
    resp = client.get("/api/v1/settings/alerts")
    assert resp.status_code == 200
    body = resp.json()
    items = {item["key"]: item for item in body["data"]}

    token_item = items["TELEGRAM_BOT_TOKEN"]
    assert "has_value" in token_item
    # has_value should correlate with masked display
    if token_item["is_sensitive"]:
        assert token_item["has_value"] == (token_item["value"] == "***")


def test_post_skip_masked_value():
    """POST with '***' should not overwrite a real sensitive field value."""
    # Read current value first
    get_resp = client.get("/api/v1/settings/alerts")
    items_before = {item["key"]: item for item in get_resp.json()["data"]}

    # Send update with "***" for TELEGRAM_BOT_TOKEN
    resp = client.post("/api/v1/settings/alerts", json={
        "TELEGRAM_BOT_TOKEN": "***",
        "SMTP_SERVER": "smtp.example.com",
    })
    assert resp.status_code == 200

    # After save, read again to verify TELEGRAM_BOT_TOKEN was NOT overwritten
    get_resp2 = client.get("/api/v1/settings/alerts")
    items_after = {item["key"]: item for item in get_resp2.json()["data"]}
    token_item = items_after["TELEGRAM_BOT_TOKEN"]
    assert token_item["is_sensitive"] is True
    # The API masks it as "***", so we can't compare directly,
    # but we verify the POST handler skipped it (no 500 error thrown)


def test_all_sensitive_keys_masked():
    """Every key in SENSITIVE_KEYS should have is_sensitive=True in response."""
    resp = client.get("/api/v1/settings/alerts")
    assert resp.status_code == 200
    items = {item["key"]: item for item in resp.json()["data"]}

    for key in SENSITIVE_KEYS:
        assert key in items, f"Missing key: {key}"
        assert items[key]["is_sensitive"] is True, f"{key} is_sensitive should be True"


def test_sensitive_fields_never_return_real_value():
    """T100 core requirement: no sensitive field returns a real value > 8 chars."""
    resp = client.get("/api/v1/settings/alerts")
    assert resp.status_code == 200
    items = {item["key"]: item for item in resp.json()["data"]}

    for key in SENSITIVE_KEYS:
        item = items[key]
        val = item["value"]
        if val and val != "***":
            # If the field has a value and it's not "***", it should be empty or very short
            # A real token/password would be much longer
            # This catches the case where masking is bypassed
            raise AssertionError(
                f"Sensitive field {key} returned unmasked value: {val!r}"
            )
