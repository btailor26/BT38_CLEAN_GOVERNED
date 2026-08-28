from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALIGNMENT = (ROOT / "services" / "governed_order_clarity_alignment.py").read_text(encoding="utf-8")
NOTIFICATION_ALIGNMENT = (
    ROOT / "services" / "governed_notification_read_alignment.py"
).read_text(encoding="utf-8")
FBM = (ROOT / "templates" / "fbm.html").read_text(encoding="utf-8")


def test_journey_numbers_are_removed_at_render_without_changing_state_authority():
    assert '("1 · Picked up", "Picked up")' in ALIGNMENT
    assert '("2 · In transit", "In transit")' in ALIGNMENT
    assert '("3 · Delivered", "Delivered")' in ALIGNMENT
    assert "journey_state = item.shipment_state" in FBM
    assert "picked_up = journey_state in ['accepted','in_transit','out_for_delivery','delivered']" in FBM
    assert "in_transit = journey_state in ['in_transit','out_for_delivery','delivered']" in FBM
    assert "delivered = journey_state == 'delivered'" in FBM


def test_historical_orders_use_the_same_persisted_rules_without_age_cutoff():
    assert "MarketplaceOrder.store_id" in ALIGNMENT
    assert "MarketplaceOrder.marketplace_order_id" in ALIGNMENT
    assert "FBMOrderProfile.store_id" in ALIGNMENT
    assert "FBMOrderProfile.marketplace_order_id" in ALIGNMENT
    assert "created_at" not in ALIGNMENT


def test_prime_badge_authority_remains_persisted_profile_truth():
    assert "FBMOrderProfile.is_prime" in ALIGNMENT
    assert "FBMOrderProfile.fulfillment_channel" in ALIGNMENT
    assert "if is_prime is True:" in ALIGNMENT
    assert 'return "Amazon · Prime"' in ALIGNMENT
    assert "shipping.prime_locked" in FBM
    assert "prime-badge.svg" in FBM


def test_bell_has_clear_persisted_order_type_without_guessing_unknown_history():
    assert 'return "Amazon · FBA"' in ALIGNMENT
    assert 'return "Amazon · FBM"' in ALIGNMENT
    assert 'return "eBay · FBM"' in ALIGNMENT
    assert 'return "Amazon · Order"' in ALIGNMENT
    assert 'return "eBay · Order"' in ALIGNMENT
    assert "_canonical_fulfillment" in ALIGNMENT


def test_all_market_fbm_tracking_is_visible_from_any_persisted_order_or_shipment_row():
    assert "_persisted_tracking_by_order_row" in ALIGNMENT
    assert "MarketplaceOrder.tracking_number" in ALIGNMENT
    assert "MarketplaceOrder.carrier" in ALIGNMENT
    assert "FBMShipment.tracking_number" in ALIGNMENT
    assert "FBMShipment.carrier" in ALIGNMENT
    assert "FBMShipment.provider" in ALIGNMENT
    assert "_enrich_fbm_tracking_html" in ALIGNMENT
    assert "bt38-db-tracking" in ALIGNMENT
    assert '"source": "marketplace_order"' in ALIGNMENT
    assert '"source": "fbm_shipment"' in ALIGNMENT
    assert 'platform == "amazon"' not in ALIGNMENT.split("def _persisted_tracking_by_order_row", 1)[1].split("def _sale_identity", 1)[0]
    assert 'platform == "ebay"' not in ALIGNMENT.split("def _persisted_tracking_by_order_row", 1)[1].split("def _sale_identity", 1)[0]


def test_alignment_is_display_only_and_does_not_touch_mcf_execution():
    assert "@app.after_request" in ALIGNMENT
    assert "db.session.add" not in ALIGNMENT
    assert "db.session.commit" not in ALIGNMENT
    assert "requests." not in ALIGNMENT
    assert "process_marketplace_notification" not in ALIGNMENT
    assert "governed_mcf" not in ALIGNMENT
    assert "MCFOrder" not in ALIGNMENT
    assert 'if fulfillment == "MCF" or fulfillment.startswith("MCF_"):' in ALIGNMENT


def test_alignment_is_installed_through_existing_notification_ui_path():
    assert "install_governed_order_clarity_alignment" in NOTIFICATION_ALIGNMENT
    assert "install_governed_order_clarity_alignment(app)" in NOTIFICATION_ALIGNMENT
    assert "from app import app" not in NOTIFICATION_ALIGNMENT
