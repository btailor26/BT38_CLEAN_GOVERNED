from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALIGNMENT = (ROOT / "services" / "governed_fbm_page_alignment.py").read_text(encoding="utf-8")
LEGACY_ROUTE = (ROOT / "governed_fbm_routes.py").read_text(encoding="utf-8")


def test_ready_to_ship_replaces_only_existing_shipping_options_read_endpoint():
    assert 'shipping_endpoint = "governed_fbm.fbm_shipping_options"' in ALIGNMENT
    assert "app.view_functions[shipping_endpoint] = persisted_fbm_shipping_options" in ALIGNMENT
    assert '@governed_fbm_bp.get("/fbm/shipping-options")' in LEGACY_ROUTE


def test_ready_to_ship_uses_persisted_batched_state_only():
    handler = ALIGNMENT.split("def persisted_fbm_shipping_options", 1)[1]
    assert "profiles = _profile_map(rows)" in handler
    assert "lines_by_order = _order_lines_map(rows)" in handler
    assert "mappings = _pack_mapping_by_sku(lines_by_order)" in handler
    assert "_persisted_parcel(lines, mappings)" in handler
    assert "_shipping_provider_options(row, profile, None)" in handler
    assert "_amazon_profile(" not in handler
    assert "get_or_refresh_amazon_profile" not in handler
    assert "parcel_from_db(" not in handler
    assert "order_lines(" not in handler
    assert "requests." not in handler
    assert "db.session.add" not in handler
    assert "db.session.commit" not in handler


def test_ebay_ready_to_ship_does_not_require_profile_refresh_or_marketplace_hydration():
    assert "platform = _platform(row)" in ALIGNMENT
    assert '"prime_profile_error": None' in ALIGNMENT
    assert '"message": "Shipping routes prepared from persisted BT38 order facts."' in ALIGNMENT
    assert "_FBM_SHIPPING_SELECTION_MAX = 50" in ALIGNMENT
