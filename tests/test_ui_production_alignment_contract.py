from pathlib import Path


CONTROLLER = Path("static/js/bt38-page-controller.js")
PRODUCT_LINKING_SESSION = Path("static/js/product-linking-session.js")
PRODUCT_LINKING = Path("templates/product_linking.html")
WAREHOUSE = Path("templates/warehouse.html")
WAREHOUSE_ROUTE = Path("governed_routes.py")


def _source(path):
    return path.read_text(encoding="utf-8")


def test_product_linking_has_one_authoritative_session_controller():
    shared = _source(CONTROLLER)
    session = _source(PRODUCT_LINKING_SESSION)

    assert 'owner: "product-linking-session.js"' in shared
    assert "installLocalProductLinkingSearch" not in shared
    assert "configureCompleteWorkingSets" not in shared
    assert "wireAsyncProductLinking" not in shared
    assert "window.searchWarehouseForLinking = function" in session
    assert "window.filterFlatListings = function" in session


def test_product_linking_session_uses_daily_snapshot_then_paginates_locally():
    source = _source(PRODUCT_LINKING_SESSION)

    assert "FULL_DATASET_LIMIT = 5000" in source
    assert "CACHE_TTL_MS = 24 * 60 * 60 * 1000" in source
    assert "fetchFullSnapshotOnceDaily" in source
    assert "window.indexedDB.open" in source
    assert "state.filtered = state.products.filter" in source
    assert "state.filtered.slice(start, start + state.perPage)" in source
    assert "state.perPage: 25" not in source
    assert "perPage: 25" in source


def test_product_linking_changes_reload_authoritative_server_state():
    source = _source(PRODUCT_LINKING_SESSION)

    # Successful relationship mutations must reload Product Linking from
    # authoritative server/Neon state instead of reconstructing the changed
    # relationship from stale browser cache/session state.
    assert "window.linkListingToWarehouse = async function" in source
    assert "window.unlinkListing = async function" in source

    # Both successful Link and Unlink paths force a full reload.
    assert source.count("window.location.reload();") >= 2

    # The old mutation-side targeted rehydration must not be used by Link/Unlink.
    link_start = source.index("window.linkListingToWarehouse = async function")
    unlink_start = source.index("window.unlinkListing = async function", link_start)

    link_block = source[link_start:unlink_start]

    next_section = source.find("\n  function ", unlink_start + 1)
    if next_section == -1:
        next_section = len(source)

    unlink_block = source[unlink_start:next_section]

    assert "await applyMutationContract(data, {" not in link_block
    assert "await applyMutationContract(data, {" not in unlink_block

    # Full forced hydrate is also not used; browser refresh causes a normal
    # fresh page load against server truth.
    assert "await hydrate(true)" not in source

def test_shared_page_controller_searches_cached_rows_then_paginates_locally():
    source = _source(CONTROLLER)

    assert "page.filteredRows = page.rows.filter" in source
    assert "page.filteredRows.slice(start, end)" in source
    assert "page.currentPage = 1" in source
    assert "event.preventDefault()" in source
    assert "fetch(" not in source


def test_default_page_size_is_15_and_can_expand_without_server_request():
    source = _source(CONTROLLER)

    assert "const allowedPageSizes = [15, 25, 50, 100]" in source
    assert "perPage: 15" in source
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


def test_warehouse_ui_uses_loaded_rows_for_runtime_figures():
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


def test_templates_keep_required_controller_scripts():
    product_linking = _source(PRODUCT_LINKING)
    warehouse = _source(WAREHOUSE)

    assert "bt38-global-state.js" in product_linking
    assert "bt38-page-controller.js" in product_linking
    assert "bt38-page-controller.js" in warehouse
