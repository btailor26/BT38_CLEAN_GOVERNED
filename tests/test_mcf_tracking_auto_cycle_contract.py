from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALIGNMENT = (ROOT / "services/governed_mcf_tracking_startup_alignment.py").read_text(
    encoding="utf-8"
)
TRACKING = (ROOT / "services/governed_mcf_tracking.py").read_text(encoding="utf-8")
EBAY = (ROOT / "services/governed_ebay_dispatch.py").read_text(encoding="utf-8")


def test_tracking_completion_is_event_only_without_timed_refresh_cycle():
    assert "notify_governed_runtime_work(" not in ALIGNMENT
    assert "_TRACKING_RETRY_SECONDS" not in ALIGNMENT
    assert '"event_type": "mcf_tracking_refresh"' not in ALIGNMENT
    assert "periodic retry" in ALIGNMENT
    assert "startup scan" in ALIGNMENT


def test_exact_amazon_signal_completes_unforwarded_tracking_immediately():
    assert "def aligned_signal(payload: dict):" in ALIGNMENT
    assert "current_signal(payload)" in ALIGNMENT
    assert "has_unforwarded_tracking(mcf_id)" in ALIGNMENT
    assert "_forward_current_tracking_set(" in ALIGNMENT


def test_all_amazon_package_tracking_is_forwarded_to_ebay_together():
    assert "tracking_details=details" in ALIGNMENT
    assert "mark_tracking_forwarded(mcf.id)" in ALIGNMENT
    assert "for shipment in (payload or {}).get(\"fulfillmentShipments\") or []" in TRACKING
    assert "for package in package_rows" in TRACKING
    assert "<ShipmentTrackingDetails>" in EBAY
    assert "for detail in normalized" in EBAY


def test_committed_tracking_wakes_existing_orders_mcf_ui():
    assert "publish_governed_ui_event" in ALIGNMENT
    assert 'source="amazon_mcf_tracking"' in ALIGNMENT
    assert '"event_type": "mcf_tracking_updated"' in ALIGNMENT


def test_orders_mcf_ui_projects_complete_tracking_set_without_changing_scalar_db_field():
    assert "_install_multi_tracking_ui_projection" in ALIGNMENT
    assert "MCFOrder.__getattribute__" in ALIGNMENT
    assert "MarketplaceOrder.__getattribute__" in ALIGNMENT
    assert '" · ".join(numbers)' in ALIGNMENT
    assert "read-only Orders / MCF pages" in ALIGNMENT
