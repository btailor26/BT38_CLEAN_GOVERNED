from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DISPATCH_QUEUE = (ROOT / "services" / "governed_fbm_dispatch_queue_alignment.py").read_text(encoding="utf-8")
EVENT_REFRESH = (ROOT / "static" / "js" / "fbm_event_session_refresh_alignment.js").read_text(encoding="utf-8")


def test_fbm_does_not_inject_a_second_pager_or_snapshot_status_bar():
    assert "bt38-fbm-session-page" not in DISPATCH_QUEUE
    assert "session snapshot bounded" not in DISPATCH_QUEUE
    assert "var pager=document.createElement" not in DISPATCH_QUEUE
    assert "Orders per page" not in DISPATCH_QUEUE
    assert "handoffToExistingPager" in DISPATCH_QUEUE
    assert "controller.renderPage(state.name)" in DISPATCH_QUEUE


def test_unknown_rendered_rows_never_default_to_ready_to_dispatch():
    assert "queue:'unclassified'" in DISPATCH_QUEUE
    assert "queue:'ready_dispatch'" not in DISPATCH_QUEUE.split("rows.forEach(function(row)", 1)[1].split("function addWorkflowButton", 1)[0]


def test_highlighted_tab_controls_visible_truth_rows():
    assert "row.dataset.fbmQueue===active" in DISPATCH_QUEUE
    assert "row.hidden=!visible.has(row)" in DISPATCH_QUEUE
    assert "active=(legacyTab&&labels[legacyTab])?legacyTab" in DISPATCH_QUEUE
    assert "button.classList.toggle('active',selected)" in DISPATCH_QUEUE


def test_refresh_reconciles_remembered_tab_after_shared_page_controller_initialises():
    assert "reconcileLifecycleViewAfterPageController" in EVENT_REFRESH
    assert "window.setTimeout(function ()" in EVENT_REFRESH
    assert "page.ready !== true" in EVENT_REFRESH
    assert "tab.click()" in EVENT_REFRESH
    assert "controller.renderPage(page.name)" in EVENT_REFRESH
    assert "reconcileLifecycleViewAfterPageController();" in EVENT_REFRESH
    assert "setInterval" not in EVENT_REFRESH
    assert "EventSource" not in EVENT_REFRESH


def test_fbm_browser_view_remembers_tab_search_page_and_page_size():
    assert "bt38:last-view:fbm" in EVENT_REFRESH
    assert "window.localStorage.getItem(lastViewKey)" in EVENT_REFRESH
    assert "window.localStorage.setItem(lastViewKey" in EVENT_REFRESH
    assert "currentPage:" in EVENT_REFRESH
    assert "perPage:" in EVENT_REFRESH
    assert "page.currentPage = desiredPage" in EVENT_REFRESH
    assert "bt38ResultsPerPageSelect" in EVENT_REFRESH
    assert "fbm-lifecycle-tab[data-fbm-tab]" in EVENT_REFRESH
    assert "bt38FbmGlobalSearchInput" in EVENT_REFRESH


def test_cancelled_marketplace_orders_are_stated_clearly():
    assert '"cancelled": "Cancelled"' in DISPATCH_QUEUE
    assert 'return "cancelled"' in DISPATCH_QUEUE
    assert "cancelled:'Cancelled'" in DISPATCH_QUEUE
    assert "addWorkflowButton(tabBar,'cancelled','Cancelled')" in DISPATCH_QUEUE
    assert "return \"excluded\"" not in DISPATCH_QUEUE.split("def _aligned_workflow_queue_for", 1)[1].split("def _presentation", 1)[0]
