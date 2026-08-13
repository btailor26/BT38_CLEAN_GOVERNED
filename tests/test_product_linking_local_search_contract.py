from pathlib import Path


SHARED_CONTROLLER = Path("static/js/bt38-page-controller.js")
SESSION_CONTROLLER = Path("static/js/product-linking-session.js")


def _source(path):
    return path.read_text(encoding="utf-8")


def test_shared_controller_explicitly_skips_product_linking():
    source = _source(SHARED_CONTROLLER)

    assert 'root.dataset.bt38Page === "productLinking"' in source
    assert 'owner: "product-linking-session.js"' in source
    assert "return;" in source
    assert "window.searchWarehouseForLinking = function" not in source
    assert "productLinkingPerPage = 5000" not in source
    assert "wireAsyncProductLinking" not in source


def test_product_linking_session_loads_full_working_set_once_then_uses_events():
    source = _source(SESSION_CONTROLLER)

    assert "FULL_DATASET_LIMIT = 5000" in source
    assert 'fetch(`/governed/product-linking/data?' in source
    assert "fetchInitialSnapshotOnce" in source
    assert "snapshotExists" in source
    assert "CACHE_TTL_MS" not in source
    assert "if (state.hydrating) return state.hydrating" in source


def test_product_linking_search_and_pagination_are_local():
    source = _source(SESSION_CONTROLLER)

    assert "state.filtered = state.products.filter" in source
    assert "state.filtered.slice(start, start + state.perPage)" in source
    assert "state.page = 1" in source
    assert "window.bt38ProductLinkingSetPage" in source
    assert "window.renderProductLinkingPagination" in source


def test_product_linking_modal_searches_use_cached_arrays():
    source = _source(SESSION_CONTROLLER)

    assert "window.filterFlatListings = function" in source
    assert "window.searchWarehouseForLinking = function" in source
    assert "state.products.filter" in source
    assert "getLinkableListings(currentWarehouseId)" in source
    assert "/governed/product-linking/search-all-listings" not in source
    assert "/governed/product-linking/search-warehouse" not in source


def test_mutations_remain_governed_and_refresh_only_affected_record():
    source = _source(SESSION_CONTROLLER)

    assert 'fetch("/governed/product-linking/link-listing-to-warehouse"' in source
    assert "await applyMutationContract(data, {" in source
    assert "TARGETED_DATASET_LIMIT = 25" in source
    assert "await hydrate(true)" not in source
    assert "mappingExists(listingId, warehouseId, data.group_id)" in source
