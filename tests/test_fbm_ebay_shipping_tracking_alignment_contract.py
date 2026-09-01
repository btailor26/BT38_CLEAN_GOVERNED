from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_JS = ROOT / "static" / "js" / "fbm_tracking_journey.js"
JOURNEY_JS = ROOT / "static" / "js" / "fbm_tracking_journey_legacy.js"
EBAY_UI_JS = ROOT / "static" / "js" / "fbm_ebay_shipping_alignment.js"
TEMPLATE = ROOT / "templates" / "fbm.html"
ROUTES = ROOT / "governed_fbm_routes.py"
ALIGNMENT = ROOT / "services" / "governed_fbm_page_alignment.py"
NATIVE_ALIGNMENT = ROOT / "services" / "governed_ebay_native_shipping_alignment.py"


def test_ebay_shipping_button_is_owned_by_native_bt38_rates_before_legacy_handoff():
    bootstrap = BOOTSTRAP_JS.read_text(encoding="utf-8")
    ui = EBAY_UI_JS.read_text(encoding="utf-8")
    template = TEMPLATE.read_text(encoding="utf-8")

    assert 'data-provider="${p.provider}"' in template
    assert '.provider-action[data-provider="ebay_shipping"]' in ui
    assert "Get eBay rates" in ui
    assert "/ebay/rates" in ui
    assert "event.stopImmediatePropagation()" in ui
    assert "}, true);" in ui
    assert "fbm_ebay_shipping_alignment.js" in bootstrap
    assert "fbm_delivery_promise_journey_alignment.js" in bootstrap
    assert "fbm_tracking_journey_legacy.js" in bootstrap

    # Runtime ownership is determined by the bootstrap event chain. Native eBay
    # capture installs first, then the preserved core journey registers directly.
    # Delivery-promise alignment is supplemental and must not gate Amazon/Packlink
    # journey controls in the active browser session.
    assert "nativeScript.onload = loadLegacy" in bootstrap
    assert "nativeScript.onerror = loadLegacy" in bootstrap
    assert "legacy.onload = function ()" in bootstrap
    assert "loadDeliveryPromiseAlignment();" in bootstrap
    assert "document.head.appendChild(nativeScript)" in bootstrap
    native_append = bootstrap.index("document.head.appendChild(nativeScript)")
    onload_bind = bootstrap.index("nativeScript.onload = loadLegacy")
    assert onload_bind < native_append


def test_ebay_shipping_backend_exposes_native_bt38_provider_capability():
    native = NATIVE_ALIGNMENT.read_text(encoding="utf-8")

    assert '"marketplace_buy_shipping": True' in native
    assert '"available": True' in native
    assert '"label_formats": ["PDF"]' in native
    assert '"auto_print_supported": True' in native
    assert '"seller_hub_fallback": False' in native
    assert '"providers": _workspace_provider_options(row, profile)' in ALIGNMENT.read_text(encoding="utf-8")


def test_native_ebay_shipping_never_routes_rate_action_to_seller_hub():
    ui = EBAY_UI_JS.read_text(encoding="utf-8")
    native = NATIVE_ALIGNMENT.read_text(encoding="utf-8")

    assert "mesh/ord/details" not in ui
    assert "window.location.assign" not in ui
    assert "ebay.co.uk/lbr" not in native
    assert "mesh/ord/details" not in native


def test_tracking_journey_preserves_purchased_provider_authority_on_packlink_failure():
    js = JOURNEY_JS.read_text(encoding="utf-8")

    assert "purchasedProviderJourneyHtml(button, error.message)" in js
    assert "Journey source: Packlink / persisted BT38 state" in js
    assert "providerShipmentFromRow" in js
    assert "button.dataset.journeySource = 'packlink'" in js
    assert "button.dataset.shipmentId = providerShipment.shipmentId" in js
    assert "marketplaceTrackingLink" in js


def test_alignment_does_not_add_inventory_or_mcf_path():
    bootstrap = BOOTSTRAP_JS.read_text(encoding="utf-8")
    ui = EBAY_UI_JS.read_text(encoding="utf-8")
    template = TEMPLATE.read_text(encoding="utf-8")
    routes = ROUTES.read_text(encoding="utf-8")
    native = NATIVE_ALIGNMENT.read_text(encoding="utf-8")

    assert "ReviseInventoryStatus" not in bootstrap
    assert "ReviseInventoryStatus" not in ui
    assert "ReviseInventoryStatus" not in template
    assert "ReviseInventoryStatus" not in native
    assert "MCF" not in bootstrap
    assert "MCF" not in ui
    assert "fulfillment not in {\"FBA\", \"AFN\", \"MCF\"}" in routes
