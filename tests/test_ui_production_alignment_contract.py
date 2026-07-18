from pathlib import Path


CONTROLLER = Path("static/js/bt38-page-controller.js")
PRODUCT_LINKING = Path("templates/product_linking.html")
WAREHOUSE = Path("templates/warehouse.html")
WAREHOUSE_ROUTE = Path("governed_routes.py")


def _source(path):
    return path.read_text(encoding="utf-8")


def test_product_linking_loads_one_complete_working_set_before_async_loader():
    source = _source(CONTROLLER)
    assert "function configureCompleteWorkingSets()" in source
    assert "productLinkingPerPage = 5000" in source
    assert "configureCompleteWorkingSets();" in source
    assert source.index("configureCompleteWorkingSets();") < source.index(
        'document.addEventListener("DOMContentLoaded"'
    )


def test_product_linking_modal_search_is_replaced_with_browser_cache():
    source = _source(CONTROLLER)
    assert "window.searchWarehouseForLinking = function" in source
    assert "window.allWarehouseProducts" in source
    assert "renderWarehouseInModal(filtered" in source
    assert "/governed/product-linking/search-warehouse" not in source


def test_page_controller_searches_complete_cached_rows_then_paginates_locally():
    source = _source(CONTROLLER)
    assert "page.filteredRows = page.rows.filter" in source
    assert "page.filteredRows.slice(start, end)" in source
    assert "page.currentPage = 1" in source
    assert "event.preventDefault()" in source
    assert "fetch(" not in source


def test_default_page_size_is_15_and_can_expand_without_server_request():
    source = _source(CONTROLLER)
    assert "const allowedPageSizes = [15, 25, 50, 100]" in source
    assert 'perPage: name === "productLinking" ? 25 : 15' in source
    assert "allowedPageSizes.includes(parsed) ? parsed : 15" in source
    assert 'select.addEventListener("change"' in source


def test_warehouse_route_loads_complete_relevant_listing_working_set_once():
    source = _source(WAREHOUSE_ROUTE)
    start = source.index("# Design B:")
    end = source.index("rows = []", start)
    block = source[start:end]
    assert "Load the complete relevant Warehouse dataset once" in block
    assert ".all()" in block
    assert ".limit(" not in block
    assert ".offset(" not in block


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
