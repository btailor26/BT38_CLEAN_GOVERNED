from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALIGNMENT = (ROOT / "services" / "governed_shipping_spend_alignment.py").read_text(encoding="utf-8")


def test_packlink_spend_requires_existing_purchase_proof():
    assert 'shipment.purchase_status or "").lower() != "purchased"' in ALIGNMENT
    assert "shipment.label_purchased_at is None" in ALIGNMENT


def test_packlink_spend_uses_exact_persisted_selected_rate():
    assert "shipment.selected_rate_id" in ALIGNMENT
    assert 'provider="packlink"' in ALIGNMENT
    assert "quote.rates or []" in ALIGNMENT
    assert 'source="packlink_purchased_selected_rate"' in ALIGNMENT


def test_pending_packlink_drafts_are_not_backfilled():
    assert '.filter_by(provider="packlink", purchase_status="purchased")' in ALIGNMENT
    assert ".filter(FBMShipment.label_purchased_at.isnot(None))" in ALIGNMENT


def test_packlink_status_wires_live_paid_label_to_same_ledger():
    assert 'endpoint = "governed_fbm.packlink_shipment_status"' in ALIGNMENT
    assert 'payload.get("payment_complete") is True' in ALIGNMENT
    assert "recover_confirmed_packlink_spend(shipment)" in ALIGNMENT
