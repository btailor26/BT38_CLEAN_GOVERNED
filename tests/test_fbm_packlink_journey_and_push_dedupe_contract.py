from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICES_INIT = (ROOT / "services" / "__init__.py").read_text(encoding="utf-8")
PACKLINK_JOURNEY = (ROOT / "services" / "governed_fbm_packlink_journey_authority.py").read_text(encoding="utf-8")
WEBHOOK_ALIGNMENT = (ROOT / "services" / "governed_webhook_alignment.py").read_text(encoding="utf-8")


def test_packlink_provider_identity_keeps_existing_journey_source():
    assert "import services.governed_fbm_packlink_journey_authority" in SERVICES_INIT
    assert "_original_bt38_owns_shipment(shipment)" in PACKLINK_JOURNEY
    assert 'provider != "packlink"' in PACKLINK_JOURNEY
    assert 'getattr(shipment, "provider_shipment_id", None)' in PACKLINK_JOURNEY
    assert 'getattr(shipment, "tracking_number", None)' in PACKLINK_JOURNEY
    assert "return bool(provider_shipment_id and tracking_number)" in PACKLINK_JOURNEY
    assert "lifecycle.bt38_owns_shipment = _aligned_bt38_owns_shipment" in PACKLINK_JOURNEY


def test_packlink_journey_authority_adds_no_parallel_runtime_or_marketplace_write():
    for forbidden in (
        "Thread(",
        "Queue(",
        "setInterval(",
        "EventSource(",
        "requests.get(",
        "requests.post(",
        "submit_governed_marketplace_action(",
        "MarketplaceOrder(",
        "FBMShipment(",
    ):
        assert forbidden not in PACKLINK_JOURNEY


def test_unchanged_persisted_marketplace_quantity_is_alignment_success():
    assert 'last_marketplace_qty = getattr(listing, "last_marketplace_qty", None)' in WEBHOOK_ALIGNMENT
    assert "marketplace_quantity_ok" in WEBHOOK_ALIGNMENT
    assert "quantity_ok = pushed_quantity_ok or marketplace_quantity_ok" in WEBHOOK_ALIGNMENT
    assert "marketplace_quantity_ok\n            or _text(getattr(listing, \"last_push_status\", None)).lower() == \"success\"" in WEBHOOK_ALIGNMENT


def test_alignment_still_retries_only_actual_exact_mismatch():
    assert "if misaligned_ids:" in WEBHOOK_ALIGNMENT
    assert "push_group_listings(" in WEBHOOK_ALIGNMENT
    assert "push_marketplace_listing(" in WEBHOOK_ALIGNMENT
    assert '"full_scan_started": False' in WEBHOOK_ALIGNMENT
    assert '"warehouse_scan_started": False' in WEBHOOK_ALIGNMENT
    assert '"marketplace_hydration_started": False' in WEBHOOK_ALIGNMENT
