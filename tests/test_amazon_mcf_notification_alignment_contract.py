from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_mcf_notification_reuses_existing_sqs_transport_without_eventbridge():
    source = _read("services/governed_amazon_mcf_notification_alignment.py")

    assert 'MCF_NOTIFICATION_TYPE = "FULFILLMENT_ORDER_STATUS"' in source
    assert "_queue_identity" in source
    assert "create_destination" in source
    assert '"transport": "sp_api_sqs"' in source
    assert '"new_queue_created": False' in source
    assert '"new_consumer_created": False' in source
    assert '"new_importer_created": False' in source
    assert "boto3.client(\"events\"" not in source
    assert "put_rule(" not in source
    assert "put_targets(" not in source
    assert "_ensure_partner_bus" not in source
    assert "_ensure_destination" not in source


def test_mcf_queue_permission_adds_spapi_direct_delivery_without_replacing_existing_policy():
    source = _read("services/governed_amazon_mcf_notification_alignment.py")

    assert 'MCF_QUEUE_POLICY_SID = "BT38AmazonSPAPISQSSendMessage"' in source
    assert 'SPAPI_SQS_PRINCIPAL = "arn:aws:iam::437568002678:root"' in source
    assert '"sqs:GetQueueAttributes"' in source
    assert '"sqs:SendMessage"' in source
    assert "row.get(\"Sid\") == MCF_QUEUE_POLICY_SID" in source
    assert '"listing_eventbridge_untouched": True' in source


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


def test_post_submit_confirmation_is_exact_but_cannot_start_fba_propagation():
    source = _read("services/governed_mcf_confirmation.py")
    mutation = _read("services/governed_order_stock_mutation.py")

    assert "refresh_mcf_status(mcf)" in source
    assert '"fba_exact_verifications_queued": 0' in source
    assert '"fba_verification_waiting_for_amazon_webhook": True' in source
    assert '"full_scan_started": False' in source
    assert '"marketplace_write_started": False' in source
    assert "notify_governed_runtime_work" not in source
    assert "AmazonSPAPIAdapter" not in source
    assert "run_governed_amazon_inventory_import" not in source
    assert "confirm_exact_mcf_after_submission" in mutation


def test_fba_quantity_is_never_derived_from_source_marketplace_sale():
    source = _read("services/governed_mcf_confirmation.py")

    assert "expected_quantity" not in source
    assert "available_quantity" not in source
    assert "quantity -" not in source
    assert '"marketplace_write_started": False' in source


def test_mcf_page_reuses_shared_live_signal_and_opens_no_second_eventsource():
    source = _read("templates/mcf_orders.html")

    assert "bt38-marketplace-event" in source
    assert "new EventSource(" not in source
    assert "/governed/ui/events/stream" not in source


def test_warehouse_sync_does_not_force_second_page_request():
    source = _read("static/js/warehouse-governed.js")

    sync_tail = source[source.index("governedWarehouseSyncBtn"):]
    assert "/governed/warehouse/sync" in sync_tail
    assert "window.location.reload()" not in sync_tail
    assert "marketplace listing/inventory hydration" not in sync_tail


def test_mcf_tracking_auto_cycle_keeps_processing_orders_alive_and_forwards_all_packages():
    alignment = _read("services/governed_mcf_tracking_startup_alignment.py")
    tracking = _read("services/governed_mcf_tracking.py")
    ebay = _read("services/governed_ebay_dispatch.py")

    assert '_TRACKING_RETRY_SECONDS = 15 * 60' in alignment
    assert '"event_type": "mcf_tracking_refresh"' in alignment
    assert 'retry = not terminal' in alignment
    assert 'amazon_status not in _TERMINAL_AMAZON' in alignment
    assert 'tracking_details=tracking_details' in alignment
    assert 'mark_tracking_forwarded(mcf.id)' in alignment
    assert 'has_unforwarded_tracking(mcf.id)' in alignment
    assert 'tracking_received_inside_one_hour_cancellation_window' in alignment
    assert 'for shipment in (payload or {}).get("fulfillmentShipments") or []' in tracking
    assert 'for package in package_rows' in tracking
    assert '<ShipmentTrackingDetails>' in ebay
    assert 'for detail in normalized' in ebay
