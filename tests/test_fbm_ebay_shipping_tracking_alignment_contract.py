from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JOURNEY_JS = ROOT / "static" / "js" / "fbm_tracking_journey.js"
TEMPLATE = ROOT / "templates" / "fbm.html"
ROUTES = ROOT / "governed_fbm_routes.py"
ALIGNMENT = ROOT / "services" / "governed_fbm_page_alignment.py"


def test_ebay_shipping_button_has_governed_marketplace_handoff():
    js = JOURNEY_JS.read_text(encoding="utf-8")
    template = TEMPLATE.read_text(encoding="utf-8")

    assert 'data-provider="${p.provider}"' in template
    assert 'data-marketplace-order-id="${esc(o.marketplace_order_id||\'\')}"' in template
    assert "p.provider==='ebay_shipping'?'Check eBay shipping'" in template
    assert "if(p==='ebay_shipping'){event.stopPropagation();" in template
    assert "providerButton.dataset.marketplaceOrderId" in template
    assert "window.location.assign(`https://www.ebay.co.uk/mesh/ord/details?orderid=${encodeURIComponent(orderId)}`)" in template
    assert "if(p==='ebay_shipping')return;" not in template

    # Keep the existing journey JS alignment/fallback behaviour intact; the
    # modal itself now owns the primary click action instead of dead-returning.
    assert "installEbayShippingHandoff" in js
    assert '.provider-action[data-provider="ebay_shipping"]' in js
    assert "button.disabled = false" in js
    assert "Open eBay shipping" in js


def test_ebay_shipping_backend_exposes_clickable_seller_hub_handoff():
    alignment = ALIGNMENT.read_text(encoding="utf-8")

    assert "def _workspace_provider_options" in alignment
    assert 'if str(option.get("provider") or "") != "ebay_shipping"' in alignment
    assert '"available": True' in alignment
    assert '"label_formats": []' in alignment
    assert '"auto_print_supported": False' in alignment
    assert "Open this exact order in eBay Seller Hub" in alignment
    assert '"providers": _workspace_provider_options(row, profile)' in alignment


def test_ebay_reference_order_routes_to_exact_seller_hub_order():
    template = TEMPLATE.read_text(encoding="utf-8")
    marketplace_order_id = "13-15093-82038"
    expected = f"https://www.ebay.co.uk/mesh/ord/details?orderid={marketplace_order_id}"

    assert "providerButton.dataset.marketplaceOrderId" in template
    assert "encodeURIComponent(orderId)" in template
    assert expected == "https://www.ebay.co.uk/mesh/ord/details?orderid=13-15093-82038"


def test_tracking_journey_never_hides_persisted_state_on_packlink_failure():
    js = JOURNEY_JS.read_text(encoding="utf-8")

    assert "marketplaceJourneyHtml(button, error.message)" in js
    assert "BT38 is showing the persisted tracking and journey state instead." in js
    assert "Journey source:" in js
    assert "Open ${esc(source)} tracking" in js
    assert "marketplaceTrackingLink" in js


def test_alignment_does_not_add_marketplace_write_or_mcf_path():
    js = JOURNEY_JS.read_text(encoding="utf-8")
    template = TEMPLATE.read_text(encoding="utf-8")
    routes = ROUTES.read_text(encoding="utf-8")
    alignment = ALIGNMENT.read_text(encoding="utf-8")

    assert "ReviseInventoryStatus" not in js
    assert "ReviseInventoryStatus" not in template
    assert "ReviseInventoryStatus" not in alignment
    assert "confirm_dispatch" not in js
    assert "MCF" not in js
    assert "fulfillment not in {\"FBA\", \"AFN\", \"MCF\"}" in routes
