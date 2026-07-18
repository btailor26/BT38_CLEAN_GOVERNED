from pathlib import Path


CONTROLLER = Path("static/js/bt38-page-controller.js")
PRODUCT_LINKING = Path("templates/product_linking.html")
WAREHOUSE = Path("templates/warehouse.html")


def _source(path):
    return path.read_text(encoding="utf-8")


def test_product_linking_modal_search_is_replaced_with_browser_cache():
    source = _source(CONTROLLER)
    assert "window.searchWarehouseForLinking = function" in source
    assert "window.allWarehouseProducts" in source
    assert "renderWarehouseInModal(filtered" in source
    assert "/governed/product-linking/search-warehouse" not in source


def test_page_controller_keeps_search_and_filters_in_memory():
    source = _source(CONTROLLER)
    assert "page.rows.filter" in source
    assert "event.preventDefault()" in source
    assert "window.location.href = route" in source
    assert "fetch(" not in source


def test_warehouse_ui_removes_hardcoded_runtime_figures_at_render_time():
    source = _source(CONTROLLER)
    assert '=== "listings"' in source
    assert '=== "inventory value"' in source
    assert "Loaded marketplace listings" in source
    assert "Loaded stock × price" in source


def test_warehouse_navigation_is_wired_to_existing_pages():
    source = _source(CONTROLLER)
    assert '"master stock": "/warehouse"' in source
    assert '"fba read only": "/fba-inventory"' in source
    assert '"group view": "/product-linking"' in source
    assert '"orders": "/orders"' in source


def test_existing_templates_still_load_the_shared_page_controller():
    product_linking = _source(PRODUCT_LINKING)
    warehouse = _source(WAREHOUSE)
    assert "bt38-page-controller.js" in product_linking
    assert "bt38-page-controller.js" in warehouse
