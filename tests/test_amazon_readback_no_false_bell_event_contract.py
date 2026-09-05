from pathlib import Path


READBACK = Path(
    "services/governed_amazon_tracking_readback.py"
).read_text(encoding="utf-8")


def test_amazon_readback_does_not_create_marketplace_shipment_rows():
    assert "from fbm_models import FBMShipment" not in READBACK
    assert "def _persist_marketplace_shipment(" not in READBACK
    assert "FBMShipment(" not in READBACK
    assert 'provider="marketplace"' not in READBACK


def test_unchanged_amazon_truth_does_not_commit_or_emit_false_activity():
    assert "if updates:\n        db.session.commit()" in READBACK
    assert "if updates or shipment_persisted" not in READBACK
    assert "last_provider_checked_at = observed_at" not in READBACK
    assert '"marketplace_shipment_persisted": False' in READBACK
    assert '"marketplace_shipment_id": None' in READBACK
