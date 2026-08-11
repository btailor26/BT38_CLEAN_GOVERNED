from pathlib import Path

SOURCE = Path("static/js/product-linking-session.js").read_text(encoding="utf-8")
GLOBAL_STATE = Path("static/js/bt38-global-state.js").read_text(encoding="utf-8")
BASE = Path("templates/base.html").read_text(encoding="utf-8")


def test_product_linking_keeps_one_existing_session_owner():
    assert "product-linking-session.js" in GLOBAL_STATE
    assert "product-linking-runtime-alignment.js" not in BASE
    assert "product-linking-session-preflight.js" not in GLOBAL_STATE


def test_product_linking_never_hydrates_5000_rows():
    assert "FULL_DATASET_LIMIT" not in SOURCE
    assert "5000" not in SOURCE
    assert "PAGE_SIZES = [15, 25, 50, 100]" in SOURCE
    assert "per_page: String(rowLimit)" in SOURCE
    assert "limit: String(rowLimit)" in SOURCE


def test_product_linking_uses_server_page_not_local_hidden_snapshot():
    assert "loadVisiblePage" in SOURCE
    assert "fetchDataset(requestedSearch, state.perPage, page)" in SOURCE
    assert "renderWarehouseProducts(state.filtered)" in SOURCE
    assert "state.filtered.slice(" not in SOURCE


def test_product_linking_search_is_live_and_debounced_in_same_controller():
    assert 'searchInput.addEventListener("input"' in SOURCE
    assert "LIVE_SEARCH_DEBOUNCE_MS = 350" in SOURCE
    assert "loadVisiblePage(1, value)" in SOURCE
    assert "requestSubmit" not in SOURCE


def test_committed_event_updates_only_exact_identity_and_moves_it_first():
    assert "if (contract && contract.changed === false) return contract" in SOURCE
    assert "for (const key of keys)" in SOURCE
    assert "fetchDataset(key, TARGETED_DATASET_LIMIT, 1)" in SOURCE
    assert "changedProducts.concat(remainingProducts).slice(0, state.perPage)" in SOURCE
