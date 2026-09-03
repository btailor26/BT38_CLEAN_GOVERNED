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
    assert "button.classList.toggle('active',selected)" in DISPATCH_QUEUE
    assert "button.addEventListener('click',function(){active=name;saveSession();render();})" in DISPATCH_QUEUE


def test_fbm_refresh_restores_exact_session_tab_after_shared_page_controller_initialises():
    assert "reconcileSessionAfterPageController" in EVENT_REFRESH
    assert "applySessionTabAfterPageController" in EVENT_REFRESH
    assert "getSessionState({tab: 'ready_dispatch'})" in EVENT_REFRESH
    assert "session && session.tab" in EVENT_REFRESH
    assert "selectedTab.click()" in EVENT_REFRESH
    assert "if (applySessionTabAfterPageController()) return;" in EVENT_REFRESH
    assert "window.addEventListener('load'" in EVENT_REFRESH
    assert "applySessionTabAfterPageController();" in EVENT_REFRESH
    assert "{once: true}" in EVENT_REFRESH
    assert "reconcileReadyAfterPageController" not in EVENT_REFRESH
    assert "localStorage" not in EVENT_REFRESH
    assert "setInterval" not in EVENT_REFRESH
    assert "EventSource" not in EVENT_REFRESH


def test_cancelled_marketplace_orders_are_stated_clearly():
    assert '"cancelled": "Cancelled"' in DISPATCH_QUEUE
    assert 'return "cancelled"' in DISPATCH_QUEUE
    assert "cancelled:'Cancelled'" in DISPATCH_QUEUE
    assert "addWorkflowButton(tabBar,'cancelled','Cancelled')" in DISPATCH_QUEUE


def test_ready_and_dispatched_use_exact_marketplace_lifecycle_not_shipping_evidence():
    classifier = DISPATCH_QUEUE.split("def _aligned_workflow_queue_for", 1)[1].split("def _health_route_state_from_marketplace_lifecycle", 1)[0]
    assert "status in _DISPATCHED_MARKETPLACE_STATUSES" in classifier
    assert "term in status" not in classifier
    assert "tracking_number" not in classifier
    assert "shipped_at" not in classifier
    assert "label_purchased_at" not in classifier
    assert "carrier_accepted_at" not in classifier
    assert "first_movement_at" not in classifier
    assert "delivered_at" not in classifier
    assert '"unshipped"' not in DISPATCH_QUEUE.split("_DISPATCHED_MARKETPLACE_STATUSES", 1)[1].split("}", 1)[0]


def test_shipping_health_uses_same_marketplace_lifecycle_classifier_as_tabs():
    assert "page_alignment._route_state = _health_route_state_from_marketplace_lifecycle" in DISPATCH_QUEUE
    health_bridge = DISPATCH_QUEUE.split("def _health_route_state_from_marketplace_lifecycle", 1)[1].split("global_search.workflow_queue_for", 1)[0]
    assert "_aligned_workflow_queue_for(row)" in health_bridge
    assert 'if queue == "dispatched"' in health_bridge
    assert 'if queue == "ready_dispatch"' in health_bridge
    assert 'if queue == "cancelled"' in health_bridge
