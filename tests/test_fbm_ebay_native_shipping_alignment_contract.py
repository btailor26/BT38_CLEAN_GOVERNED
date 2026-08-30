from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALIGNMENT = ROOT / "services" / "governed_ebay_native_shipping_alignment.py"
UI = ROOT / "static" / "js" / "fbm_ebay_shipping_alignment.js"
MAIN = ROOT / "main.py"
FBM = ROOT / "governed_fbm_routes.py"
MODELS = ROOT / "fbm_models.py"
QZ = ROOT / "static" / "js" / "fbm_qz_print.js"


def test_native_ebay_shipping_reuses_existing_fbm_state_and_provider_slot():
    alignment = ALIGNMENT.read_text(encoding="utf-8")
    fbm = FBM.read_text(encoding="utf-8")
    models = MODELS.read_text(encoding="utf-8")

    assert 'provider="ebay_shipping"' in alignment
    assert 'provider="ebay_shipping"' not in models
    assert "FBMRateQuote" in alignment
    assert "FBMShipment" in alignment
    assert '"provider": "ebay_shipping"' in fbm
    assert '"marketplace_buy_shipping": True' in alignment
    assert '"auto_print_supported": True' in alignment
    assert '"label_formats": ["PDF"]' in alignment


def test_native_ebay_shipping_calls_only_official_logistics_endpoints():
    alignment = ALIGNMENT.read_text(encoding="utf-8")

    assert 'EBAY_LOGISTICS_BASE_URL = "https://api.ebay.com/sell/logistics/v1_beta"' in alignment
    assert 'f"{EBAY_LOGISTICS_BASE_URL}/shipping_quote"' in alignment
    assert 'f"{EBAY_LOGISTICS_BASE_URL}/shipment/create_from_shipping_quote"' in alignment
    assert 'download_label_file' in alignment
    assert '"X-EBAY-C-MARKETPLACE-ID": EBAY_MARKETPLACE_ID' in alignment
    assert 'EBAY_MARKETPLACE_ID = "EBAY_GB"' in alignment
    assert "ebay.co.uk/lbr" not in alignment
    assert "mesh/ord/details" not in alignment


def test_ebay_purchase_is_duplicate_charge_guarded_before_provider_write():
    alignment = ALIGNMENT.read_text(encoding="utf-8")

    assert 'purchase_key = f"ebay_shipping:{order.store_id}:{order.marketplace_order_id}"' in alignment
    assert 'purchase_status="pending"' in alignment
    assert "db.session.commit()" in alignment
    assert "_create_shipment(order" in alignment
    assert 'purchase_status = "verification_required"' in alignment
    assert "automatic retry" in alignment.lower()
    assert 'marketplace_confirmation_status = "ebay_shipping_managed_by_ebay"' in alignment
    assert "CompleteSale" in alignment
    assert "Do not\n        # issue a second CompleteSale write" in alignment


def test_ebay_ui_supersedes_seller_hub_handoff_in_capture_phase():
    ui = UI.read_text(encoding="utf-8")

    assert '.provider-action[data-provider="ebay_shipping"]' in ui
    assert "Get eBay rates" in ui
    assert "/ebay/rates" in ui
    assert "/ebay/purchase" in ui
    assert "BUY_POSTAGE" in ui
    assert "event.stopImmediatePropagation()" in ui
    assert "}, true);" in ui
    assert "mesh/ord/details" not in ui
    assert "window.location.assign" not in ui


def test_ebay_label_reuses_existing_qz_print_bridge_and_is_installed_after_fbm_alignment():
    ui = UI.read_text(encoding="utf-8")
    qz = QZ.read_text(encoding="utf-8")
    main = MAIN.read_text(encoding="utf-8")
    alignment = ALIGNMENT.read_text(encoding="utf-8")

    assert "BT38FBMQZ.printLabel(payload.label)" in ui
    assert "async function printLabel(label)" in qz
    assert 'format === \'PDF\'' in qz
    assert "install_governed_notification_read_alignment(app)" in main
    assert "install_governed_ebay_native_shipping_alignment(app)" in main
    assert main.index("install_governed_notification_read_alignment(app)") < main.index("install_governed_ebay_native_shipping_alignment(app)")
    assert 'fbm_ebay_shipping_alignment.js' in alignment


def test_limited_release_or_scope_failure_never_falls_back_to_browser_purchase():
    alignment = ALIGNMENT.read_text(encoding="utf-8")
    ui = UI.read_text(encoding="utf-8")

    assert "limited_release_required" in alignment
    assert "authorization_required" in alignment
    assert '"seller_hub_fallback": False' in alignment
    assert "No postage purchase was attempted" in ui
