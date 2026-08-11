from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TABS = (ROOT / "templates" / "_inventory_area_tabs.html").read_text(encoding="utf-8")


def test_inventory_tabs_use_canonical_active_pages():
    assert 'href="/warehouse"' in TABS
    assert 'href="/product-linking"' in TABS
    assert 'href="/amazon-fba-stock"' in TABS
    assert "url_for('governed_mcf.orders_mcf_page')" in TABS


def test_inventory_tabs_do_not_route_orders_or_fba_through_legacy_shortcuts():
    assert '<a href="/groups"' not in TABS
    assert 'href="/warehouse?view=fba"' not in TABS
