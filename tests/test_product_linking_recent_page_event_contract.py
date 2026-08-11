from pathlib import Path


SESSION = Path("static/js/product-linking-session.js")


def _source():
    return SESSION.read_text(encoding="utf-8")


def test_product_linking_never_hydrates_5000_idle_groups():
    source = _source()
    assert "FULL_DATASET_LIMIT" not in source
    assert "5000" not in source
    assert "const PAGE_SIZES = [15, 25, 50, 100]" in source
    assert "Math.min(Number.parseInt(limit || state.perPage" in source


def test_landing_and_expanded_pages_are_server_paged_not_client_full_dataset():
    source = _source()
    assert 'await loadVisiblePage(1, "")' in source
    assert "fetchDataset(requestedSearch, state.perPage, page)" in source
    assert "window.bt38ProductLinkingSetPage = function" in source
    assert "window.bt38ProductLinkingSetPageSize = function" in source


def test_search_queries_governed_endpoint_and_keeps_existing_renderer():
    source = _source()
    assert "loadVisiblePage(1, getFilters().search)" in source
    assert "/governed/product-linking/data?" in source
    assert "renderWarehouseProducts(state.filtered)" in source


def test_changed_group_moves_to_top_and_idle_groups_are_not_requeried():
    source = _source()
    assert "changedProducts.concat(remainingProducts).slice(0, state.perPage)" in source
    assert "for (const key of keys)" in source
    assert "fetchDataset(key, TARGETED_DATASET_LIMIT, 1)" in source
    assert "if (contract && contract.changed === false) return contract" in source
