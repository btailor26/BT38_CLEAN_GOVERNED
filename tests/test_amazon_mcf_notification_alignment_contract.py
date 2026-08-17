from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_mcf_notification_reuses_existing_eventbridge_and_sqs_transport():
    source = _read("services/governed_amazon_mcf_notification_alignment.py")

    assert 'MCF_NOTIFICATION_TYPE = "FULFILLMENT_ORDER_STATUS"' in source
    assert "_queue_identity" in source
    assert "_ensure_destination" in source
    assert '"target": "existing_amazon_sqs"' in source
    assert '"new_queue_created": False' in source
    assert '"new_consumer_created": False' in source
    assert '"new_importer_created": False' in source


def test_mcf_notification_does_not_modify_listing_notification_topics():
    listing = _read("services/governed_amazon_listing_fulfillment_refresh.py")
    mcf = _read("services/governed_amazon_mcf_notification_alignment.py")

    assert '"LISTINGS_ITEM_STATUS_CHANGE"' in listing
    assert '"LISTINGS_ITEM_MFN_QUANTITY_CHANGE"' in listing
    assert "FULFILLMENT_ORDER_STATUS" not in listing
    assert "LISTINGS_ITEM_STATUS_CHANGE" not in mcf
    assert "LISTINGS_ITEM_MFN_QUANTITY_CHANGE" not in mcf


def test_amazon_mcf_status_bypasses_listing_resolution_and_reuses_existing_handler():
    source = _read("services/governed_webhook_execution.py")

    mcf_pos = source.index('== "FULFILLMENT_ORDER_STATUS"')
    listing_pos = source.index("listing = _find_listing(")
    assert mcf_pos < listing_pos
    assert "refresh_mcf_from_amazon_signal(payload)" in source
    assert '"mcf_fulfillment_status_processed"' in source


def test_post_submit_confirmation_is_exact_and_never_scans_inventory():
    source = _read("services/governed_mcf_confirmation.py")
    mutation = _read("services/governed_order_stock_mutation.py")

    assert "refresh_mcf_status(mcf)" in source
    assert '"event_type": "fba_inventory_alignment"' in source
    assert '"marketplace": "amazon_fba"' in source
    assert '"full_scan_started": False' in source
    assert "AmazonSPAPIAdapter" not in source
    assert "run_governed_amazon_inventory_import" not in source
    assert "confirm_exact_mcf_after_submission" in mutation


def test_fba_quantity_is_never_derived_from_source_marketplace_sale():
    source = _read("services/governed_mcf_confirmation.py")

    assert "expected_quantity" not in source
    assert "available_quantity" not in source
    assert "quantity -" not in source
    assert "marketplace_write_started\": False" in source
