from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
READ = (ROOT / "services/governed_operational_table_read_alignment.py").read_text(encoding="utf-8")
TEMPLATE = (ROOT / "templates/mcf_orders.html").read_text(encoding="utf-8")


def test_mcf_page_uses_shared_bounded_server_read():
    assert 'if path == "/orders-mcf"' in READ
    assert "_mcf_orders_page()" in READ
    assert "ALLOWED_PAGE_SIZES = (15, 25, 50, 100)" in READ
    assert "candidate_limit = min(250, (page * per_page) + 1)" in READ
    assert "page_rows = filtered[start:start + per_page]" in READ


def test_mcf_browser_does_not_own_hidden_100_row_pagination():
    assert "filtered.slice(" not in TEMPLATE
    assert "let page =" not in TEMPLATE
    assert "mcf-page-size" not in TEMPLATE
    assert 'id="bt38ResultsPerPageSelect"' in TEMPLATE
    assert "[15,25,50,100]" in TEMPLATE


def test_mcf_filters_are_server_targeted_and_live():
    assert 'name="status"' in TEMPLATE
    assert 'name="search"' in TEMPLATE
    assert "window.setTimeout(submitFilter, 350)" in TEMPLATE
    assert "form.requestSubmit()" in TEMPLATE


def test_mcf_exact_event_queries_only_exact_order_identity():
    assert "url.searchParams.set('search', orderId)" in TEMPLATE
    assert "X-BT38-UI-Refresh': 'mcf-exact-order" in TEMPLATE
    assert "window.location.href" not in TEMPLATE.split("bt38-marketplace-event", 1)[1].split("})();", 1)[0]


def test_mcf_business_actions_remain_existing_governed_routes():
    assert "governed_mcf.remove_selected_mcf_orders" in TEMPLATE
    assert "governed_mcf.refresh_mcf_order" in TEMPLATE
    assert "governed_mcf.order_mcf_detail_page" in TEMPLATE
