from pathlib import Path


SESSION = Path("static/js/product-linking-session.js")


def source():
    return SESSION.read_text(encoding="utf-8")


def test_full_product_linking_snapshot_is_limited_to_once_per_24_hours():
    code = source()
    assert "const CACHE_TTL_MS = 24 * 60 * 60 * 1000" in code
    assert "snapshotIsFresh" in code
    assert "fetchFullSnapshotOnceDaily" in code
    assert 'navigator.locks.request("bt38-product-linking-daily-snapshot"' in code
    assert "if (snapshotIsFresh(cached)) applySnapshot(cached)" in code


def test_daily_snapshot_is_persisted_in_indexeddb_not_session_only_memory():
    code = source()
    assert 'const CACHE_DB_NAME = "bt38-browser-cache"' in code
    assert 'const CACHE_STORE_NAME = "snapshots"' in code
    assert 'const CACHE_KEY = "product-linking-v2"' in code
    assert "window.indexedDB.open" in code
    assert "writeSnapshot" in code
    assert "readSnapshot" in code


def test_page_reload_and_legacy_force_calls_do_not_force_full_database_read():
    code = source()
    assert "window.loadProductLinkingData = function ()" in code
    load_block = code[code.index("window.loadProductLinkingData = function ()"):]
    load_block = load_block[:load_block.index("window.bt38RefreshProductLinkingRecord")]
    assert "return hydrate();" in load_block
    assert "fetchFullSnapshot" not in load_block


def test_governed_change_refreshes_only_affected_record():
    code = source()
    assert "async function refreshAffectedRecord(identity)" in code
    assert "TARGETED_DATASET_LIMIT = 25" in code
    assert "await refreshAffectedRecord({ listingId, warehouseId, listingSku, warehouseSku })" in code
    mutation_block = code[code.index("window.linkListingToWarehouse = async function"):]
    assert "fetchFullSnapshot" not in mutation_block
    assert "hydrate(true)" not in mutation_block


def test_search_filter_pagination_and_modals_remain_local():
    code = source()
    assert "state.products.filter(product => productMatches(product, filters))" in code
    assert "state.filtered.slice(start, start + state.perPage)" in code
    assert "window.filterFlatListings = function" in code
    assert "window.searchWarehouseForLinking = function" in code
