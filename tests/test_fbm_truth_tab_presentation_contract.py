from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DISPATCH_QUEUE = (ROOT / "services" / "governed_fbm_dispatch_queue_alignment.py").read_text(encoding="utf-8")


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
