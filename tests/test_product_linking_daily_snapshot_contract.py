from pathlib import Path


SESSION = Path("static/js/product-linking-session.js")


def source():
    return SESSION.read_text(encoding="utf-8")


def test_full_product_linking_snapshot_is_bootstrapped_once_then_event_driven():
    code = source()
    assert "snapshotExists" in code
    assert "fetchInitialSnapshotOnce" in code
    assert "navigator.locks.request(CACHE_LOCK_NAME" in code
    assert "await fetchInitialSnapshotOnce()" in code


def test_daily_snapshot_is_persisted_in_indexeddb_not_session_only_memory():
    code = source()
    assert 'const CACHE_DB_NAME = "bt38-browser-cache"' in code
    assert 'const CACHE_STORE_NAME = "snapshots"' in code
    assert 'const CACHE_KEY = `product-linking-session-v7:${RELEASE_VERSION}`' in code
    assert "snapshot.releaseVersion === RELEASE_VERSION" in code
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


def test_governed_mutations_refresh_only_affected_rows():
    code = source()
    assert "async function applyMutationContract(contract, identity)" in code
    assert "async function refreshAffectedRecord(identity)" in code
    assert "TARGETED_DATASET_LIMIT = 25" in code
    mutation_block = code[
        code.index("async function applyMutationContract(contract, identity)"):
        code.index("async function refreshAffectedRecord(identity)")
    ]
    assert "mutationSearchKeys" in mutation_block
    assert "fetchDataset(key, TARGETED_DATASET_LIMIT)" in mutation_block
    assert "mergeTargetedData(data, listingIds)" in mutation_block
    assert "fetchFullSnapshot" not in mutation_block
    assert 'fetchDataset("", FULL_DATASET_LIMIT)' not in mutation_block
    link_block = code[code.index("window.linkListingToWarehouse = async function"):]
    assert "await applyMutationContract(data" in link_block
    assert "hydrate(true)" not in link_block
    assert "fetchFullSnapshot" not in link_block


def test_search_filter_pagination_and_modals_remain_local():
    code = source()
    assert "state.products.filter((product) => productMatches(product, filters))" in code
    assert "state.filtered.slice(start, start + state.perPage)" in code
    assert "window.filterFlatListings = function" in code
    assert "window.searchWarehouseForLinking = function" in code


def test_explicit_zero_result_search_can_recover_a_missed_listing_event():
    code = source()
    recovery = code[
        code.index("async function recoverMissingSearchFromDatabase(search)"):
        code.index("function mappingExists(")
    ]
    assert "state.filtered.length > 0" in recovery
    assert "fetchDataset(exactSearch, TARGETED_DATASET_LIMIT)" in recovery
    assert "fetchFullSnapshot" not in recovery


def test_no_background_polling_or_timer_driven_full_scan():
    code = source()
    assert "setInterval(" not in code
    assert "setTimeout(" not in code
