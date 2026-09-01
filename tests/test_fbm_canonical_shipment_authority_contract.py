from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROUTES = (ROOT / "governed_fbm_routes.py").read_text(encoding="utf-8")
TEMPLATE = (ROOT / "templates" / "fbm.html").read_text(encoding="utf-8")
JS = (ROOT / "static" / "js" / "fbm_tracking_journey_legacy.js").read_text(encoding="utf-8")


def _shipment_map_source() -> str:
    start = ROUTES.index("def _shipment_map(")
    end = ROUTES.index("\ndef _get_fbm_order", start)
    return ROUTES[start:end]


def test_fbm_uses_one_db_first_canonical_shipment_authority():
    source = _shipment_map_source()

    assert "The database is the only authority exposed to the FBM page" in source
    assert "order_tracking_by_key" in source
    assert "persisted_order_tracking" in source
    assert "exact_tracking_match" in source
    assert "tracking_number == persisted_order_tracking" in source
    assert "canonical_authority_rank" in source

    # Returns/replacements are separate physical shipments and must not replace
    # the original outbound journey unless persisted order tracking identifies
    # that exact shipment.
    assert '"packlink_return:"' in source
    assert '"packlink_replacement:"' in source
    assert "exact_tracking_match" in source.split("return (", 1)[1]

    # Page selection is persisted DB logic only. Provider reads happen later on
    # the already-selected Packlink shipment status route.
    assert "PacklinkAdapter(" not in source
    assert "get_tracking_status(" not in source
    assert "get_shipment(" not in source


def test_fbm_provider_journey_receives_the_selected_persisted_shipment_id():
    assert "shipment.provider == 'packlink' and shipment.provider_shipment_id and tracking_number" in TEMPLATE
    assert 'data-shipment-id="{{ shipment.id }}"' in TEMPLATE
    assert "/fbm/shipments/${encodeURIComponent(button.dataset.shipmentId)}/packlink/status" in JS
    assert "Journey source: Packlink / carrier platform" in JS
