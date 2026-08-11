from pathlib import Path


TEMPLATE = Path("templates/mcf_orders.html").read_text(encoding="utf-8")
ROUTES = Path("governed_mcf_routes.py").read_text(encoding="utf-8")


def test_mcf_keeps_existing_session_owner_and_live_table_filtering():
    assert "BT38.getPageSession" in TEMPLATE
    assert "BT38.setPageSession" in TEMPLATE
    assert "wantedSearch" in TEMPLATE
    assert "wantedStatus" in TEMPLATE
    assert "filtered.slice" in TEMPLATE


def test_mcf_rows_selector_is_bottom_table_control():
    footer = TEMPLATE.index('<div class="card-footer')
    selector = TEMPLATE.index('id="mcf-page-size"')
    table = TEMPLATE.index('id="mcf-orders-body"')
    assert table < footer < selector
    for size in (15, 25, 50, 100):
        assert f'<option value="{size}"' in TEMPLATE
    assert "Results per page:" in TEMPLATE


def test_mcf_exact_event_refresh_and_actions_are_not_replaced():
    assert "bt38-marketplace-event" in TEMPLATE
    assert "mcf-exact-order" in TEMPLATE
    assert "refresh_mcf_order" in TEMPLATE
    assert "remove_selected_mcf_orders" in TEMPLATE
    assert '@governed_mcf_bp.get("/orders-mcf")' in ROUTES
    assert "orders = _bulk_orders(limit=100)" in ROUTES


def test_no_second_mcf_table_controller_or_read_route_added():
    assert "governed_operational_table_read_alignment" not in ROUTES
    assert "before_request" not in ROUTES
