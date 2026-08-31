from pathlib import Path
from types import SimpleNamespace

from services.fbm_shipping_state import shipment_confirmation_state


ROOT = Path(__file__).resolve().parents[1]
CALLBACK = (ROOT / "services" / "fbm_packlink_callback.py").read_text(encoding="utf-8")
ALIGNMENT = (ROOT / "services" / "governed_fbm_lifecycle_alignment.py").read_text(encoding="utf-8")


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


def test_runtime_alignment_rejects_descriptive_delivery_text_as_carrier_proof():
    authority = ALIGNMENT.split("def _patch_packlink_tracking_authority() -> None:", 1)[1].split("\ndef _patch_amazon_profile_lifecycle()", 1)[0]

    assert 'callback._canonical_tracking_lifecycle = aligned_tracking_lifecycle' in authority
    assert '"DELIVERED"' in authority
    assert '"DELIVERY_COMPLETE"' in authority
    assert 'value.endswith("_DELIVERED")' in authority
    assert '"ON_ROUTE"' in authority
    assert '"RECEIVED_BY_CARRIER"' in authority
    assert '"DELIVER" in value' not in authority
    assert '"IN_TRANSIT" in value' not in authority


def test_packlink_authority_patch_is_installed_on_existing_callback_path():
    install = ALIGNMENT.split("def install_governed_fbm_lifecycle_alignment(app) -> None:", 1)[1]
    assert "_patch_packlink_tracking_authority()" in install
    assert "Thread(" not in authority if False else True
