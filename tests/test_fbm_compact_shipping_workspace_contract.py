from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / "templates" / "fbm.html").read_text(encoding="utf-8")


def test_fbm_restores_full_workspace_presentation():
    assert '<div class="row g-3 mb-4">' in TEMPLATE
    assert 'fbm-health-ring' not in TEMPLATE
    assert 'fbm-overview-grid' not in TEMPLATE
    assert '<table class="table table-hover align-middle mb-0">' in TEMPLATE


def test_full_order_table_columns_are_preserved():
    table = TEMPLATE.split('<table class="table table-hover align-middle mb-0">', 1)[1].split('</table>', 1)[0]
    assert '<th>Marketplace</th>' in table
    assert '<th>Order</th>' in table
    assert '<th>Product</th>' in table
    assert '<th>Qty</th>' in table
    assert '<th>Postcode</th>' in table
    assert '<th>Recommended shipping</th>' in table
    assert '<th>Shipment</th>' in table
    assert '<th>Journey</th>' in table
    assert '<th>Action</th>' in table


def test_existing_shipping_actions_remain_explicit():
    assert 'id="readyToShipSelected"' in TEMPLATE
    assert 'class="btn btn-sm btn-outline-primary fbm-shipping-options"' in TEMPLATE
    assert "openShippingOptions([b.dataset.orderId])" in TEMPLATE


def test_existing_packlink_and_qz_controls_remain_on_page():
    assert 'id="packlinkConnectionTest"' in TEMPLATE
    assert 'id="qzConnect"' in TEMPLATE
    assert 'id="qzPrinter"' in TEMPLATE
    assert 'id="qzAutoPrint"' in TEMPLATE


def test_journey_truth_is_not_changed_by_presentation_restore():
    assert "picked_up = journey_state in ['accepted','in_transit','out_for_delivery','delivered']" in TEMPLATE
    assert "in_transit = journey_state in ['in_transit','out_for_delivery','delivered']" in TEMPLATE
    assert "delivered = journey_state == 'delivered'" in TEMPLATE
    assert 'Tracking received · pickup not confirmed yet.' in TEMPLATE
    assert 'Marketplace says shipped · carrier milestones unavailable.' in TEMPLATE
