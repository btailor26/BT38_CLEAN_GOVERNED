from pathlib import Path

SOURCE = Path("static/js/product-linking-session.js").read_text(encoding="utf-8")
GLOBAL_STATE = Path("static/js/bt38-global-state.js").read_text(encoding="utf-8")
BASE = Path("templates/base.html").read_text(encoding="utf-8")


def test_product_linking_keeps_one_existing_session_owner():
    assert "product-linking-session.js" in GLOBAL_STATE
    assert "product-linking-runtime-alignment.js" not in BASE
    assert "product-linking-session-preflight.js" not in GLOBAL_STATE


def test_product_linking_bootstraps_one_complete_browser_working_set():
    assert "FULL_DATASET_LIMIT = 5000" in SOURCE
    assert "fetchInitialSnapshotOnce" in SOURCE
    assert "readSnapshot" in SOURCE
    assert "PAGE_SIZES = [15, 25, 50, 100]" in SOURCE
    assert 'navigator.locks.request("bt38-product-linking-initial-snapshot"' in SOURCE


def test_product_linking_search_and_paging_use_browser_session_rows():
    assert "loadVisiblePage" not in SOURCE
    assert "state.filtered = state.products.filter" in SOURCE
    assert "state.filtered.slice(start, start + state.perPage)" in SOURCE
    assert "renderWarehouseProducts(pageRows)" in SOURCE


def test_product_linking_search_is_live_and_local_in_same_controller():
    assert 'form.querySelectorAll("input, select")' in SOURCE
    assert "state.page = 1; render();" in SOURCE
    assert "LIVE_SEARCH_DEBOUNCE_MS" not in SOURCE
    assert "requestSubmit" not in SOURCE


def test_committed_event_updates_only_exact_identity_and_moves_it_first():
    assert "if (contract && contract.changed === false) return contract" in SOURCE
    assert "for (const key of keys)" in SOURCE
    assert "fetchDataset(key, TARGETED_DATASET_LIMIT)" in SOURCE
    assert "remainingProducts.concat(changedProducts)" in SOURCE
