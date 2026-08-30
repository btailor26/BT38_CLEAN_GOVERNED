from pathlib import Path
from types import SimpleNamespace

from services.fbm_shipping_state import shipment_confirmation_state


ROOT = Path(__file__).resolve().parents[1]
CALLBACK = (ROOT / "services" / "fbm_packlink_callback.py").read_text(encoding="utf-8")


def test_packlink_on_track_without_scan_stays_waiting_for_pickup_even_with_stale_timestamps():
    shipment = SimpleNamespace(
        provider="packlink",
        last_provider_status="On track",
        delivered_at=None,
        first_movement_at=object(),
        carrier_accepted_at=object(),
        handover_due_at=None,
        label_purchased_at=object(),
    )

    assert shipment_confirmation_state(shipment) == "awaiting_carrier_acceptance"


def test_packlink_explicit_scan_can_promote_pickup():
    shipment = SimpleNamespace(
        provider="packlink",
        last_provider_status="COLLECTED",
        delivered_at=None,
        first_movement_at=None,
        carrier_accepted_at=object(),
        handover_due_at=None,
        label_purchased_at=object(),
    )

    assert shipment_confirmation_state(shipment) == "accepted"


def test_packlink_carrier_success_is_not_physical_pickup_authority():
    helper = CALLBACK.split("def _apply_lifecycle_state", 1)[1].split("def _attach_by_marketplace_reference", 1)[0]
    carrier_success = helper.split('if event_name == "shipment.carrier.success":', 1)[1].split('elif event_name == "shipment.tracking.update":', 1)[0]

    assert "carrier_accepted_at" not in carrier_success
    assert 'shipment.status = "awaiting_carrier_acceptance"' in carrier_success
    assert "reconcile_packlink_tracking_lifecycle" in CALLBACK
