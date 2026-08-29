from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JOURNEY_JS = ROOT / "static" / "js" / "fbm_tracking_journey.js"
TEMPLATE = ROOT / "templates" / "fbm.html"
ROUTES = ROOT / "governed_fbm_routes.py"


def test_ebay_shipping_button_has_governed_marketplace_handoff():
    js = JOURNEY_JS.read_text(encoding="utf-8")
    template = TEMPLATE.read_text(encoding="utf-8")

    assert 'data-provider="${p.provider}"' in template
    assert "p.provider==='ebay_shipping'?'Check eBay shipping'" in template
    assert "installEbayShippingHandoff" in js
    assert '.provider-action[data-provider="ebay_shipping"]' in js
    assert "button.disabled = false" in js
    assert "Open eBay shipping" in js
    assert "ebay.co.uk/mesh/ord/details?orderid=" in js
    assert "window.open" in js


def test_tracking_journey_never_hides_persisted_state_on_packlink_failure():
    js = JOURNEY_JS.read_text(encoding="utf-8")

    assert "marketplaceJourneyHtml(button, error.message)" in js
    assert "BT38 is showing the persisted tracking and journey state instead." in js
    assert "Journey source:" in js
    assert "Open ${esc(source)} tracking" in js
    assert "marketplaceTrackingLink" in js


def test_alignment_does_not_add_marketplace_write_or_mcf_path():
    js = JOURNEY_JS.read_text(encoding="utf-8")
    routes = ROUTES.read_text(encoding="utf-8")

    assert "ReviseInventoryStatus" not in js
    assert "confirm_dispatch" not in js
    assert "MCF" not in js
    assert "fulfillment not in {\"FBA\", \"AFN\", \"MCF\"}" in routes
