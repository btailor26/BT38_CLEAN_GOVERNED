from pathlib import Path


READ_MODEL = Path("services/product_linking_recent_table_alignment.py")
MAIN = Path("main.py")


def _source(path):
    return path.read_text(encoding="utf-8")


def test_product_linking_pages_display_groups_not_raw_warehouse_rows():
    source = _source(READ_MODEL)
    assert "recent_group_event_table" in source
    assert "GROUP BY identity_key" in source
    assert "ORDER BY touched_at DESC" in source
    assert "per_page" in source
    assert "{15, 25, 50, 100}" in source
    assert "5000" not in source


def test_current_listing_group_absorbs_permanent_shadow_group_activity():
    source = _source(READ_MODEL)
    assert "ml.master_product_group_id IS NOT NULL" in source
    assert "NOT EXISTS" in source
    assert "ml2.master_product_group_id IS NOT NULL" in source
    assert "current MarketplaceListing.master_product_group_id" in source


def test_product_linking_recent_table_is_installed_before_route_dispatch():
    source = _source(MAIN)
    assert "install_product_linking_recent_table_alignment" in source
    assert "install_product_linking_recent_table_alignment(app)" in source


def test_search_keeps_targeted_existing_row_shape():
    source = _source(READ_MODEL)
    assert "ml.external_sku ILIKE :like" in source
    assert "ml.title ILIKE :like" in source
    assert '"warehouse_products": warehouse_products' in source
    assert '"listings": selected_listing_payloads + unlinked_listings' in source
