from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_ebay_return_topic_uses_existing_governed_registration_path():
    source = (ROOT / "services" / "governed_ebay_notification_registration.py").read_text()

    assert 'RETURN_TOPIC_ID = "ORDER_RETURN_ACTIVITY"' in source
    assert "RETURN_TOPIC_ID," in source.split("REQUIRED_TOPIC_IDS = (", 1)[1].split(")", 1)[0]
    assert "topic_id=RETURN_TOPIC_ID" in source
    assert "_ensure_subscription(" in source
    assert "_topic_schema_version(" in source
    assert 'RETURN_READ_SCOPE = "https://api.ebay.com/oauth/api_scope/sell.return.read"' in source
    assert 'RETURN_WRITE_SCOPE = "https://api.ebay.com/oauth/api_scope/sell.return"' in source


def test_ebay_return_webhook_remains_existing_non_stock_lifecycle_event():
    source = (ROOT / "services" / "governed_webhook_execution.py").read_text()

    assert 'return "return"' in source
    assert 'elif event_type == "return":' in source
    assert 'event_reason = "Return notification stored for awareness"' in source

    return_branch = source.split('elif event_type == "return":', 1)[1].split("elif ", 1)[0]
    assert "apply_order_stock_mutation" not in return_branch
    assert "warehouse" not in return_branch.lower()
