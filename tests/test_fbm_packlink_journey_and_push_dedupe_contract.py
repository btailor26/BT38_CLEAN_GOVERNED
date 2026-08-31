from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIFECYCLE = (ROOT / "services" / "governed_fbm_lifecycle_alignment.py").read_text(encoding="utf-8")
JOURNEY = (ROOT / "static" / "js" / "fbm_tracking_journey.js").read_text(encoding="utf-8")
WEBHOOK_ALIGNMENT = (ROOT / "services" / "governed_webhook_alignment.py").read_text(encoding="utf-8")


def test_packlink_journey_source_requires_deterministic_purchase_authority():
    assert 'provider == "packlink"' in LIFECYCLE
    assert 'purchase_key.startswith("packlink_")' in LIFECYCLE
    assert "provider_shipment_id and tracking_number" not in LIFECYCLE


def test_pickup_red_requires_persisted_label_or_postage_authority():
    assert "label_purchased_at=None" in LIFECYCLE
    assert "and bt38_owns_shipment(shipment)" in LIFECYCLE
    assert 'getattr(shipment, "label_url", None)' in LIFECYCLE
    assert 'getattr(shipment, "label_purchased_at", None)' in LIFECYCLE
    assert 'getattr(shipment, "tracking_number", None)' not in LIFECYCLE.split("def aligned_render_template", 1)[1].split("marker =", 1)[0]
    assert "return String(row && row.dataset ? row.dataset.labelReady || '' : '') === '1';" in JOURNEY
    assert "querySelectorAll('code')" not in JOURNEY.split("function labelOrTrackingStageReached", 1)[1].split("function setBadgeState", 1)[0]
    assert "Label / postage created · waiting for carrier collection" in JOURNEY


def test_existing_journey_colour_meanings_remain_locked():
    assert "setBadgeState(pickedUp, 'bg-success')" in JOURNEY
    assert "setBadgeState(pickedUp, 'bg-danger')" in JOURNEY
    assert "setBadgeState(inTransit, 'bg-success')" in JOURNEY
    assert "setBadgeState(delivered, 'bg-success')" in JOURNEY


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
