from pathlib import Path


SOURCE = Path(
    "services/governed_amazon_tracking_readback.py"
).read_text(encoding="utf-8")


def _persistence_block() -> str:
    return SOURCE.split(
        "def _persist_marketplace_shipment(", 1
    )[1].split(
        "def hydrate_amazon_tracking_for_order(", 1
    )[0]


def test_exact_amazon_tracking_persists_marketplace_owned_physical_shipment():
    block = _persistence_block()
    assert "from fbm_models import FBMShipment" in SOURCE
    assert "FBMShipment.store_id == store_id" in block
    assert "FBMShipment.marketplace_order_id == order_id" in block
    assert "FBMShipment.tracking_number == tracking" in block
    assert 'provider="marketplace"' in block
    assert 'marketplace_confirmation_status="marketplace_authoritative"' in block
    assert "db.session.add(row)" in block


def test_marketplace_shipment_does_not_override_bt38_owned_same_tracking():
    block = _persistence_block()
    assert '_text(existing.provider).lower() != "marketplace"' in block
    assert "return existing, False" in block
    assert "purchase_key=" not in block
    assert "label_purchased_at=" not in block


def test_marketplace_readback_persists_lifecycle_without_inventing_milestone_times():
    block = _persistence_block()
    assert "lifecycle = _text(shipment.get(\"lifecycle_status\")).lower()" in block
    assert "_can_advance_lifecycle(row.status, lifecycle)" in block
    assert "row.status = lifecycle" in block
    assert "last_provider_status=provider_status" in block
    assert "last_provider_checked_at=observed_at" in block
    assert "carrier_accepted_at" not in block
    assert "first_movement_at" not in block
    assert "delivered_at" not in block
    assert "createdTime" not in block


def test_amazon_readback_commits_marketplace_shipment_with_existing_order_update():
    hydrate = SOURCE.split("def hydrate_amazon_tracking_for_order(", 1)[1]
    assert "_persist_marketplace_shipment(" in hydrate
    assert "if updates or shipment_persisted:" in hydrate
    assert "db.session.commit()" in hydrate
    assert '"marketplace_write_started": False' in hydrate
