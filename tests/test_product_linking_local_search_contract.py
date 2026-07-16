from pathlib import Path


def test_product_linking_async_render_rebuilds_local_cache():
    controller = Path("static/js/bt38-page-controller.js").read_text(encoding="utf-8")

    assert 'wireAsyncTableRefresh(pageName)' in controller
    assert 'new MutationObserver(refresh)' in controller
    assert 'refreshTableCache(pageName)' in controller


def test_product_linking_clear_stays_local():
    controller = Path("static/js/bt38-page-controller.js").read_text(encoding="utf-8")

    assert 'pageName === "productLinking"' in controller
    assert 'a[href="/product-linking"]' in controller
    assert 'event.preventDefault()' in controller
