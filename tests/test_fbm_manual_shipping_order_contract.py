from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_manual_shipping_is_standalone_and_persists_before_packlink():
    source = (ROOT / "governed_fbm_manual_routes.py").read_text(encoding="utf-8")
    models = (ROOT / "fbm_models.py").read_text(encoding="utf-8")

    assert 'class FBMManualOrder' in models
    assert '__tablename__ = "fbm_manual_orders"' in models
    assert 'db.ForeignKey("stores.id"' not in models.split('class FBMManualOrder', 1)[1]
    assert 'db.ForeignKey("marketplace_orders' not in models.split('class FBMManualOrder', 1)[1]

    # Manual shipment creation must never masquerade as a marketplace sale.
    assert "MarketplaceOrder" not in source
    assert "WarehouseStock" not in source
    assert "stock" not in source.lower().split("from services.fbm_packlink_adapter", 1)[1]

    # Destination and parcel facts must be committed before an external rate call.
    rates_block = source.split("def manual_packlink_rates", 1)[1].split("def manual_packlink_draft", 1)[0]
    assert rates_block.index("db.session.commit()") < rates_block.index("PacklinkAdapter().get_rates")

    # Draft creation remains explicit and idempotent by stored provider reference.
    draft_block = source.split("def manual_packlink_draft", 1)[1].split("def manual_packlink_status", 1)[0]
    assert 'CREATE_MANUAL_PACKLINK_DRAFT' in draft_block
    assert "if order.provider_shipment_id" in draft_block


def test_manual_shipping_routes_are_registered_under_governed_fbm_tree():
    source = (ROOT / "governed_packlink_callback_routes.py").read_text(encoding="utf-8")
    assert "from governed_fbm_manual_routes import governed_fbm_manual_bp" in source
    assert "register_blueprint(governed_fbm_manual_bp)" in source
