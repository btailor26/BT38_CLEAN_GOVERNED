from pathlib import Path


CONTROLLER = Path("static/js/bt38-page-controller.js")


def controller_source():
    return CONTROLLER.read_text(encoding="utf-8")


def test_product_linking_loads_one_complete_browser_working_set_before_fetch():
    controller = controller_source()

    assert 'function bt38ConfigureProductLinkingWorkingSet()' in controller
    assert 'productLinkingPerPage = 5000' in controller
    assert 'bt38ConfigureProductLinkingWorkingSet();' in controller
    assert controller.index('bt38ConfigureProductLinkingWorkingSet();') < controller.index(
        'document.addEventListener("DOMContentLoaded", bt38BootPageController'
    )


def test_product_linking_async_render_rebuilds_cache_once():
    controller = controller_source()

    assert 'const refreshWhenReady = () =>' in controller
    assert 'new MutationObserver(() =>' in controller
    assert 'observer.disconnect()' in controller
    assert 'refreshTableCache(pageName)' in controller


def test_product_linking_search_and_clear_stay_local():
    controller = controller_source()

    assert 'window.BT38.PageController.localFilter(pageName)' in controller
    assert 'a[href="/product-linking"]' in controller
    assert 'event.preventDefault()' in controller
    assert 'data-bt38-local-page' in controller


def test_product_linking_keeps_local_pagination():
    controller = controller_source()

    assert 'renderProductLinkingPagination(' in controller
    assert 'pageName === "productLinking"' in controller
    assert 'page.currentPage = Math.min(Math.max(targetPage, 1), totalPages)' in controller


def test_product_linking_fix_does_not_add_backend_or_marketplace_calls():
    controller = controller_source()

    assert '/governed/product-linking/data' not in controller
    assert '/warehouse' not in controller
    assert '/api/sync-status' not in controller
