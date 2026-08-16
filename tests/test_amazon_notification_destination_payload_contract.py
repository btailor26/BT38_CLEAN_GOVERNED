from pathlib import Path


SERVICE_PATH = Path("services/governed_amazon_listing_fulfillment_refresh.py")


def test_destination_payload_accepts_list_and_wrapped_dict_shapes():
    source = SERVICE_PATH.read_text(encoding="utf-8")
    block = source.split(
        "def ensure_governed_amazon_listing_notification_subscriptions(", 1
    )[1].split(
        "def recover_governed_amazon_listing_from_notification(", 1
    )[0]

    assert "isinstance(destination_payload, list)" in block
    assert 'destination_payload.get("destinations")' in block
    assert 'get("eventBridge", {})' in block
    assert 'get("sqs", {})' not in block
