from pathlib import Path


def test_dispatched_rows_show_persisted_shipping_authority_not_route_choices():
    script = Path("static/js/fbm_tracking_journey.js").read_text(encoding="utf-8")

    assert "function alignDispatchedShippingAuthority(row, status)" in script
    assert "const shippingCell = row.children[5];" in script
    assert "const shipmentCell = row.children[7];" in script
    assert "const trackingNode = shipmentCell.querySelector('code');" in script
    assert "label.textContent = 'Dispatch authority';" in script
    assert "note.textContent = 'Persisted shipment evidence';" in script
    assert "alignDispatchedShippingAuthority(row, status);" in script


def test_dispatched_authority_comes_from_rendered_persisted_shipment_cell():
    script = Path("static/js/fbm_tracking_journey.js").read_text(encoding="utf-8")

    assert "const carrierNode = shipmentCell.querySelector('strong');" in script
    assert "authority.textContent = carrier;" in script
    assert "shippingCell.replaceChildren();" in script
