from pathlib import Path


SCRIPT = Path("static/js/fbm_tracking_journey.js").read_text(encoding="utf-8")
QZ_SCRIPT = Path("static/js/fbm_qz_print.js").read_text(encoding="utf-8")
ALIGNMENT = Path("services/governed_fbm_page_alignment.py").read_text(encoding="utf-8")


def test_shipping_options_restore_fbm_fulfilment_identity():
    assert "installFbmShippingModeSetting" in SCRIPT
    assert "fbm-shipping-mode-setting" in SCRIPT
    assert "Fulfilment" in SCRIPT
    assert ">FBM<" in SCRIPT
    assert "Choose shipping route" in SCRIPT


def test_packlink_save_button_and_browser_save_action_are_removed():
    assert "packlink-save" not in SCRIPT
    assert "/packlink/save" not in SCRIPT
    assert "SAVE_PACKLINK_DRAFT" not in SCRIPT
    assert "savePacklinkDraft" not in SCRIPT


def test_packlink_handoff_remains_available_without_save_action():
    assert "https://pro.packlink.com/" in SCRIPT
    assert "Open Packlink PRO" in SCRIPT
    assert "packlink-status" in SCRIPT


def test_shipping_options_open_is_selected_order_persisted_read_only():
    assert 'shipping_options_endpoint = "governed_fbm.fbm_shipping_options"' in ALIGNMENT
    assert "MarketplaceOrder.id.in_(order_ids)" in ALIGNMENT
    assert "profiles = _profile_map(amazon_rows)" in ALIGNMENT
    assert "get_or_refresh_amazon_profile" not in ALIGNMENT
    assert "_amazon_profile(" not in ALIGNMENT
    handler = ALIGNMENT.split("def bounded_shipping_options", 1)[1]
    assert "parcel_from_db(" not in handler
    assert "order_lines(" not in handler
    assert '"parcel": _selected_row_parcel(row)' in handler
    assert "Complete provider reads remain deferred until an explicit shipping action." in ALIGNMENT
    assert "app.view_functions[shipping_options_endpoint] = bounded_shipping_options" in ALIGNMENT


def test_saved_pack_mapping_parcel_is_rehydrated_without_broad_order_hydration():
    assert "ProductPackMapping" in ALIGNMENT
    assert "def _persisted_pack_mapping_parcel" in ALIGNMENT
    assert ".filter_by(single_sku=sku, is_active=True)" in ALIGNMENT
    for field in ("carton_weight_kg", "carton_length_cm", "carton_width_cm", "carton_height_cm"):
        assert field in ALIGNMENT
    helper = ALIGNMENT.split("def _persisted_pack_mapping_parcel", 1)[1].split("def _selected_row_parcel", 1)[0]
    assert "MarketplaceOrder.query" not in helper
    assert "WarehouseStock.query" not in helper
    assert "order_lines(" not in helper


def test_fbm_browser_requests_have_hard_timeout_and_clean_recovery():
    assert "FBM_FETCH_TIMEOUT_MS = 15000" in QZ_SCRIPT
    assert "new AbortController()" in QZ_SCRIPT
    assert "controller.abort()" in QZ_SCRIPT
    assert "parsed.pathname.startsWith('/fbm/')" in QZ_SCRIPT
    assert "parsed.pathname.startsWith('/governed/fbm/')" in QZ_SCRIPT
    assert "Shipping request timed out after 15 seconds. Please try again." in QZ_SCRIPT
    assert "global.fetch = wrappedFetch" in QZ_SCRIPT
