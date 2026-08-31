from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / "templates" / "fbm.html").read_text(encoding="utf-8")
PROMISE = (ROOT / "services" / "fbm_db_delivery_promise_alignment.py").read_text(encoding="utf-8")


def test_marketplace_customer_location_is_not_exposed_in_fbm_table():
    table = TEMPLATE.split('<table class="table table-hover align-middle mb-0 fbm-orders-table">', 1)[1].split('</table>', 1)[0]
    assert '<th>Postcode</th>' not in table
    assert '{{ order.ship_to_postcode' not in table
    assert '<th>Shipping</th>' in table
    assert '<th>Ship / deliver</th>' in table


def test_existing_marketplace_icon_assets_are_used():
    assert "img/marketplaces/amazon.png" in TEMPLATE
    assert "img/marketplaces/ebay.png" in TEMPLATE
    assert "img/marketplaces/shopify.png" in TEMPLATE
    assert "img/marketplaces/tiktok.png" in TEMPLATE


def test_shipping_promise_uses_existing_persisted_marketplace_fields():
    assert "promise.shipping_service" in TEMPLATE
    assert "promise.ship_by_at" in TEMPLATE
    assert "promise.latest_delivery_at" in TEMPLATE
    assert "shipping_service" in PROMISE
    assert "ship_by_at" in PROMISE
    assert "latest_delivery_at" in PROMISE
    assert "fbm_order_operational_state" in PROMISE


def test_printer_is_visible_beside_fbm_shipping_actions_without_second_qz_path():
    assert 'id="fbmPrinterToggle"' in TEMPLATE
    assert "document.querySelector('.fbm-shipping-setup')" in TEMPLATE
    assert "setup.open=true" in TEMPLATE
    assert TEMPLATE.count("BT38FBMQZ.connect()") == 1


def test_marketplace_postcode_is_hidden_in_shipping_choice_but_own_site_can_show_it():
    assert "/^(website|web|direct|shopify|woocommerce)$/i" in TEMPLATE
    assert "ownWebsite&&o.postcode" in TEMPLATE
    assert "Qty ${o.quantity||0}${customerLocation}" in TEMPLATE
    assert "No postcode" not in TEMPLATE


def test_journey_is_compact_without_changing_lifecycle_truth():
    assert 'class="fbm-journey-steps"' in TEMPLATE
    assert "picked_up = journey_state in ['accepted','in_transit','out_for_delivery','delivered']" in TEMPLATE
    assert "in_transit = journey_state in ['in_transit','out_for_delivery','delivered']" in TEMPLATE
    assert "delivered = journey_state == 'delivered'" in TEMPLATE
