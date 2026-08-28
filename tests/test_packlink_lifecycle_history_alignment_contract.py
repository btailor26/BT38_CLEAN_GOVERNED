from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CALLBACK = (ROOT / "services" / "fbm_packlink_callback.py").read_text(encoding="utf-8")
POST_PURCHASE = (ROOT / "services" / "fbm_post_purchase.py").read_text(encoding="utf-8")


def test_tracking_history_can_promote_terminal_delivery_truth():
    assert "def _canonical_tracking_lifecycle" in CALLBACK
    assert 'return "DELIVERED"' in CALLBACK
    assert 'return "IN_TRANSIT"' in CALLBACK
    assert 'return "ACCEPTED"' in CALLBACK
    assert "tracking_history" in CALLBACK
    assert "reconcile_provider_lifecycle_state" in CALLBACK


def test_confirmed_historical_packlink_rows_refresh_lifecycle_without_marketplace_write():
    assert "marketplace_confirmed_at is not None" in CALLBACK
    assert '"lifecycle_only": True' in CALLBACK
    assert '"marketplace_write_attempted": False' in CALLBACK
    recovery = CALLBACK.split("def recover_packlink_shipments_for_day", 1)[1]
    assert "FBMShipment.marketplace_confirmed_at.is_(None)" not in recovery


def test_shared_journey_remains_timestamp_authority():
    assert "shipment.delivered_at = shipment.delivered_at or observed_at" in POST_PURCHASE
    assert "shipment.first_movement_at = shipment.first_movement_at or observed_at" in POST_PURCHASE
    assert "shipment.carrier_accepted_at = shipment.carrier_accepted_at or observed_at" in POST_PURCHASE


def test_no_created_at_cutoff_is_added_to_lifecycle_classification():
    lifecycle = CALLBACK.split("def _canonical_tracking_lifecycle", 1)[1].split("def _first_label_url", 1)[0]
    assert "created_at" not in lifecycle
