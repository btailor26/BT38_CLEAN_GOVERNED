from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / "main.py").read_text(encoding="utf-8")
ALIGNMENT = (ROOT / "services" / "governed_fbm_ready_landing_alignment.py").read_text(encoding="utf-8")


def test_ready_to_dispatch_is_the_first_fbm_landing_view():
    assert "var sessionDefaults={tab:'ready_dispatch',search:'',dirty:false};" in ALIGNMENT
    assert "saved.tab!=='pending'" in ALIGNMENT
    assert "?saved.tab:'ready_dispatch'" in ALIGNMENT
    assert "addWorkflowButton(tabBar,'ready_dispatch','Ready to dispatch');" in ALIGNMENT
    assert "addWorkflowButton(tabBar,'pending','Pending');" in ALIGNMENT
    assert ALIGNMENT.index("addWorkflowButton(tabBar,'ready_dispatch','Ready to dispatch');") < ALIGNMENT.index("addWorkflowButton(tabBar,'pending','Pending');")


def test_ready_landing_reuses_existing_rendered_snapshot_only():
    assert "current()" in ALIGNMENT
    assert "response.get_data(as_text=True)" in ALIGNMENT
    for forbidden in (
        "db.session",
        "MarketplaceOrder.query",
        "requests.",
        "new EventSource",
        "setInterval(",
        "Thread(",
        "Queue(",
    ):
        assert forbidden not in ALIGNMENT


def test_ready_landing_installs_after_existing_small_fbm_overlay():
    small_call = "install_governed_fbm_small_alignment(app)"
    ready_call = "install_governed_fbm_ready_landing_alignment(app)"
    assert small_call in MAIN
    assert ready_call in MAIN
    assert MAIN.index(small_call) < MAIN.index(ready_call)
