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

    # Link commits through the governed route, then reloads Product Linking
    # from authoritative server/Neon state.
    assert "window.linkListingToWarehouse = async function" in source

    # Unlink is intentionally two-stage: the row action only prepares the
    # dedicated confirmation modal, and only the explicit confirmation
    # function may issue the governed unlink POST.
    assert "window.unlinkListing = function" in source
    assert "async function confirmExplicitUnlink()" in source

    link_start = source.index("window.linkListingToWarehouse = async function")
    unlink_prepare_start = source.index("window.unlinkListing = function", link_start)
    unlink_confirm_start = source.index("async function confirmExplicitUnlink()", unlink_prepare_start)
    wire_start = source.index("function wire()", unlink_confirm_start)

    link_block = source[link_start:unlink_prepare_start]
    unlink_prepare_block = source[unlink_prepare_start:unlink_confirm_start]
    unlink_confirm_block = source[unlink_confirm_start:wire_start]

    # Clicking a listing's Unlink control must never mutate the relationship.
    assert "fetch(" not in unlink_prepare_block
    assert "pendingExplicitUnlink = {" in unlink_prepare_block

    # Only explicit confirmation may POST the unlink mutation.
    assert "/unlink" in unlink_confirm_block
    assert "user_confirmed: true" in unlink_confirm_block

    # Link and confirmed Unlink both clear stale browser state and reload
    # from committed server/Neon truth.
    assert "await clearSnapshot();" in link_block
    assert "window.location.reload();" in link_block
    assert "await clearSnapshot();" in unlink_confirm_block
    assert "window.location.reload();" in unlink_confirm_block

    # Old mutation-side targeted rehydration must not be used by either
    # relationship mutation path.
    assert "await applyMutationContract(data, {" not in link_block
    assert "await applyMutationContract(data, {" not in unlink_confirm_block

    # Full forced hydrate is also not used; browser reload performs the fresh
    # authoritative read after a committed mutation.
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
