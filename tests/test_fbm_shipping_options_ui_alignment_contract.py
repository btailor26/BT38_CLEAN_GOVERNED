from pathlib import Path


SCRIPT = Path("static/js/fbm_tracking_journey.js").read_text(encoding="utf-8")
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
    assert "profiles = _profile_map(list(by_id.values()))" in ALIGNMENT
    assert "get_or_refresh_amazon_profile" not in ALIGNMENT
    assert "_amazon_profile(" not in ALIGNMENT
    assert "Live provider reads remain deferred until an explicit shipping action." in ALIGNMENT
