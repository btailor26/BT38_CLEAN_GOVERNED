from pathlib import Path


RUNTIME = Path("static/js/product-linking-runtime-alignment.js")
SESSION = Path("static/js/product-linking-session.js")


def test_product_linking_live_search_is_debounced_not_button_only():
    runtime = RUNTIME.read_text(encoding="utf-8")
    assert "LIVE_SEARCH_DEBOUNCE_MS = 350" in runtime
    assert 'input.addEventListener("input"' in runtime
    assert "form.requestSubmit()" in runtime


def test_live_search_reuses_existing_targeted_server_paged_submit_path():
    runtime = RUNTIME.read_text(encoding="utf-8")
    session = SESSION.read_text(encoding="utf-8")
    assert "loadVisiblePage(1, getFilters().search)" in session
    assert "/governed/product-linking/data?" in session
    assert "5000" not in session
    assert "targeted governed query" in runtime
