from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALIGNMENT = (ROOT / "services/governed_mcf_tracking_startup_alignment.py").read_text(
    encoding="utf-8"
)
TRACKING = (ROOT / "services/governed_mcf_tracking.py").read_text(encoding="utf-8")
EBAY = (ROOT / "services/governed_ebay_dispatch.py").read_text(encoding="utf-8")


def test_tracking_auto_refresh_reuses_exact_governed_event_loop():
    assert '"event_type": "mcf_tracking_refresh"' in ALIGNMENT
    assert "_TRACKING_RETRY_SECONDS = 15 * 60" in ALIGNMENT
    assert "notify_governed_runtime_work(" in ALIGNMENT
    assert "new_worker_started\": False" in ALIGNMENT
    assert "new_scheduler_started\": False" in ALIGNMENT


def test_processing_mcf_keeps_polling_for_later_split_packages():
    assert "retry = not terminal" in ALIGNMENT
    assert "amazon_status not in _TERMINAL_AMAZON" in ALIGNMENT
    assert "_queue_tracking_refresh(" in ALIGNMENT
    assert "mcf_tracking_current_set_already_forwarded" in ALIGNMENT


def test_all_amazon_package_tracking_is_forwarded_to_ebay_together():
    assert "tracking_details=tracking_details" in ALIGNMENT
    assert "mark_tracking_forwarded(mcf.id)" in ALIGNMENT
    assert "for shipment in (payload or {}).get(\"fulfillmentShipments\") or []" in TRACKING
    assert "for package in package_rows" in TRACKING
    assert "<ShipmentTrackingDetails>" in EBAY
    assert "for detail in normalized" in EBAY


def test_restart_recovery_does_not_treat_one_scalar_tracking_value_as_complete():
    assert "has_unforwarded_tracking(mcf.id)" in ALIGNMENT
    assert "_tracking_enrichment_complete(lines, mcf)" in ALIGNMENT
    assert "Processing MCF orders remain live" in ALIGNMENT
    assert "tracking_refreshes_queued" in ALIGNMENT


def test_dispatched_order_does_not_restart_cancellation_window_on_tracking_refresh():
    assert "shipped_at proves that cancellation window has already ended" in ALIGNMENT
    assert "source_marketplace_dispatch_pending" in ALIGNMENT
    assert "tracking_received_inside_one_hour_cancellation_window" in ALIGNMENT
