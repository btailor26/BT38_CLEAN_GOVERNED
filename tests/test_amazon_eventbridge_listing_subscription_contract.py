from pathlib import Path


SERVICE_PATH = Path("services/governed_amazon_listing_fulfillment_refresh.py")


def _source() -> str:
    return SERVICE_PATH.read_text(encoding="utf-8")


def test_listing_notifications_select_existing_eventbridge_destination_only():
    source = _source()
    function_block = source.split(
        "def ensure_governed_amazon_listing_notification_subscriptions(", 1
    )[1].split(
        "def recover_governed_amazon_listing_from_notification(", 1
    )[0]

    assert 'get("eventBridge", {})' in function_block
    assert 'get("sqs", {})' not in function_block
    assert 'amazon_existing_eventbridge_destination_missing' in function_block
    assert '"destination_created": False' in function_block


def test_listing_notification_types_remain_exact_and_canonical_writer_unchanged():
    source = _source()

    assert '"LISTINGS_ITEM_STATUS_CHANGE"' in source
    assert '"LISTINGS_ITEM_MFN_QUANTITY_CHANGE"' in source
    assert "refresh_governed_listing_from_snapshot(" in source
    assert "refresh_governed_amazon_listing_exact(" in source
