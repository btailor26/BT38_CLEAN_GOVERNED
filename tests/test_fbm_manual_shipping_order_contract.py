from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_manual_shipping_is_standalone_and_persists_before_packlink():
    source = (ROOT / "governed_fbm_manual_routes.py").read_text(encoding="utf-8")
    models = (ROOT / "fbm_models.py").read_text(encoding="utf-8")

    assert 'class FBMManualOrder' in models
    assert '__tablename__ = "fbm_manual_orders"' in models
    manual_model = models.split('class FBMManualOrder', 1)[1]
    assert 'db.ForeignKey("stores.id"' not in manual_model
    assert 'db.ForeignKey("marketplace_orders' not in manual_model

    # Manual shipment creation must never import or create marketplace/warehouse records.
    assert "from models import MarketplaceOrder" not in source
    assert "from models import WarehouseStock" not in source
    assert "MarketplaceOrder(" not in source
    assert "WarehouseStock(" not in source

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


def test_manual_shipping_preserves_full_destination_and_reuses_saved_order():
    mapper = (ROOT / "services" / "fbm_order_mapper.py").read_text(encoding="utf-8")
    template = (ROOT / "templates" / "fbm_manual_shipping.html").read_text(encoding="utf-8")
    tabs = (ROOT / "templates" / "_inventory_area_tabs.html").read_text(encoding="utf-8")

    assert '"address2": _text(getattr(order, "ship_to_address2", None))' in mapper
    assert '"region": _text(getattr(order, "ship_to_region", None))' in mapper

    # Once saved, Save & get rates must continue the same manual DB order instead
    # of POSTing /fbm/manual again and creating a duplicate shipping record.
    save_block = template.split("async function saveOrder", 1)[1].split("async function prepareDraft", 1)[0]
    assert "if(!manualOrderId)" in save_block
    assert "`/fbm/manual/${manualOrderId}/packlink/rates`" in save_block
    assert "saveButton.disabled=true" in template

    assert 'href="/fbm"' in tabs
    assert 'href="/fbm/manual"' in tabs


def test_release_gates_cover_manual_shipping_runtime_and_contract():
    deploy = (ROOT / ".github" / "workflows" / "deploy-fly.yml").read_text(encoding="utf-8")
    readiness = (ROOT / ".github" / "workflows" / "deployment-readiness.yml").read_text(encoding="utf-8")

    for workflow in (deploy, readiness):
        assert "governed_fbm_manual_routes.py" in workflow
        assert "services/fbm_marketplace_destination.py" in workflow
        assert "services/governed_exact_ebay_order_hydration.py" in workflow
        assert "tests/test_fbm_manual_shipping_order_contract.py" in workflow
