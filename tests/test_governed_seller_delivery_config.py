from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL = (ROOT / "seller_delivery_models.py").read_text(encoding="utf-8")
SERVICE = (ROOT / "services" / "governed_seller_delivery_config.py").read_text(encoding="utf-8")


def test_seller_delivery_origin_is_owned_by_warehouse_not_store():
    assert 'db.ForeignKey("warehouses.id"' in MODEL
    assert "store_id" not in MODEL
    assert '"marketplace_store_owns_origin": False' in SERVICE


def test_seller_delivery_cannot_enable_without_real_origin_and_radius():
    assert "if enabled and (not origin_postcode or radius is None)" in SERVICE
    assert "Origin postcode and delivery radius are required" in SERVICE
    assert "Default Location" not in SERVICE


def test_seller_delivery_cost_is_explicit_not_automatically_zero():
    assert 'cost_mode not in {"manual", "flat", "per_mile"}' in SERVICE
    assert 'cost_mode == "flat" and flat_cost is None' in SERVICE
    assert 'cost_mode == "per_mile" and per_mile_cost is None' in SERVICE


def test_configuration_does_not_create_shipments_or_marketplace_writes():
    assert "FBMShipment" not in SERVICE
    assert "guard_marketplace_write" not in SERVICE
    assert '"prime_sfp_allowed": False' in SERVICE
