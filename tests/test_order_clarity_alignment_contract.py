from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALIGNMENT = (ROOT / "services" / "governed_order_clarity_alignment.py").read_text(encoding="utf-8")
NOTIFICATION_ALIGNMENT = (
    ROOT / "services" / "governed_notification_read_alignment.py"
).read_text(encoding="utf-8")
FBM = (ROOT / "templates" / "fbm.html").read_text(encoding="utf-8")
FBM_JOURNEY_JS = (ROOT / "static" / "js" / "fbm_tracking_journey.js").read_text(encoding="utf-8")


def test_journey_numbers_are_removed_at_render_without_changing_state_authority():
    assert '("1 · Picked up", "Picked up")' in ALIGNMENT
    assert '("2 · In transit", "In transit")' in ALIGNMENT
    assert '("3 · Delivered", "Delivered")' in ALIGNMENT
    assert "journey_state = item.shipment_state" in FBM
    assert "picked_up = journey_state in ['accepted','in_transit','out_for_delivery','delivered']" in FBM
    assert "in_transit = journey_state in ['in_transit','out_for_delivery','delivered']" in FBM
    assert "delivered = journey_state == 'delivered'" in FBM


def test_fbm_page_alignment_never_recovers_or_requeries_persisted_state_on_get():
    assert "@app.after_request" in ALIGNMENT
    assert 'path == "/fbm"' in ALIGNMENT
    assert "_clean_fbm_journey_html" in ALIGNMENT
    assert "db.session" not in ALIGNMENT
    assert "MarketplaceOrder" not in ALIGNMENT
    assert "FBMShipment" not in ALIGNMENT
    assert "FBMOrderProfile" not in ALIGNMENT
    assert "tuple_(" not in ALIGNMENT
    assert "tracking_number" not in ALIGNMENT
    assert "delivered_at" not in ALIGNMENT
    assert "requests." not in ALIGNMENT
    assert "fetch(" not in ALIGNMENT


def test_fbm_read_boundary_is_event_persisted_not_page_hydrated():
    assert "marketplace/provider handoff owns collection and persistence" in ALIGNMENT
    assert "Never query or reconcile from a page" in ALIGNMENT
    assert "event-persisted state remains authoritative" in ALIGNMENT
    assert "db.session.add" not in ALIGNMENT
    assert "db.session.commit" not in ALIGNMENT
    assert "process_marketplace_notification" not in ALIGNMENT
    assert "governed_mcf" not in ALIGNMENT
    assert "MCFOrder" not in ALIGNMENT


def test_marketplace_tracking_clicks_stay_inside_bt38_journey_modal_without_extra_job():
    assert 'a[href*="ebay.co.uk/mesh/ord/details"]' in FBM_JOURNEY_JS
    assert 'a[href*="sellercentral.amazon.co.uk/orders-v3/order/"]' in FBM_JOURNEY_JS
    assert "link.removeAttribute('href')" in FBM_JOURNEY_JS
    assert "link.removeAttribute('target')" in FBM_JOURNEY_JS
    assert "link.dataset.journeySource = 'marketplace'" in FBM_JOURNEY_JS
    assert "marketplaceJourneyHtml(button)" in FBM_JOURNEY_JS
    assert "body.innerHTML = marketplaceJourneyHtml(button)" in FBM_JOURNEY_JS
    assert "if (button.dataset.journeySource === 'marketplace' || !button.dataset.shipmentId)" in FBM_JOURNEY_JS
    marketplace_branch = FBM_JOURNEY_JS.split("if (button.dataset.journeySource === 'marketplace' || !button.dataset.shipmentId)", 1)[1].split("body.innerHTML = '<div class=", 1)[0]
    assert "fetch(" not in marketplace_branch


def test_ebay_shipping_handoff_uses_same_tab_and_cannot_trigger_popup_blocking():
    ebay_handoff = FBM_JOURNEY_JS.split("function openEbayShipping(button)", 1)[1].split("function installManualShippingButton", 1)[0]
    assert "window.location.assign(" in ebay_handoff
    assert "window.open(" not in ebay_handoff
    assert "www.ebay.co.uk/mesh/ord/details?orderid=" in ebay_handoff


def test_alignment_is_installed_through_existing_notification_ui_path():
    assert "install_governed_order_clarity_alignment" in NOTIFICATION_ALIGNMENT
    assert "install_governed_order_clarity_alignment(app)" in NOTIFICATION_ALIGNMENT
    assert "from app import app" not in NOTIFICATION_ALIGNMENT
