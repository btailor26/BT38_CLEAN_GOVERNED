from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
ROUTES = (ROOT / "governed_routes.py").read_text(encoding="utf-8")
SIGNAL = (ROOT / "services" / "governed_ui_event_signal.py").read_text(
    encoding="utf-8"
)


def test_shared_base_has_one_global_notification_bell_and_drawer():
    assert BASE.count('id="bt38NotificationBell"') == 1
    assert BASE.count('id="bt38NotificationPanel"') == 1
    assert 'const salesUrl = "/governed/ui/notifications?limit=20"' in BASE


def test_bell_reads_persisted_events_only_on_open_without_polling_or_starting_work():
    assert '@governed_bp.get("/governed/ui/notifications")' in ROUTES
    assert 'MarketplaceOrder.query' in ROUTES
    assert 'record.platform || "Marketplace"' in BASE
    assert 'method: "GET"' in BASE
    assert "setInterval" not in BASE
    assert "void loadNotifications();" not in BASE
    assert 'panel.addEventListener("show.bs.offcanvas"' in BASE


def test_bell_lights_from_existing_marketplace_event_without_reenabling_waiter():
    assert "bt38NotificationBell" in SIGNAL
    assert 'LIVE_BROWSER_EVENT_WAITER_ENABLED = False' in SIGNAL
    assert '_condition.wait(timeout=25.0)' not in SIGNAL
    assert 'window.addEventListener("bt38-marketplace-event"' in BASE
    assert 'setBellLight(true)' in BASE
    assert 'setBellLight(false)' in BASE
    assert 'bell.classList.toggle("text-warning", active)' in BASE
