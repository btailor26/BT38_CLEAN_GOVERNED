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
    assert "governed.governed_notification_audit" in BASE


def test_bell_reads_durable_events_without_polling_or_starting_work():
    assert 'SyncLog.message.like("event_type=%")' in ROUTES
    assert '"source": "Neon SystemLog + SyncLog"' in ROUTES
    assert 'method: "GET"' in BASE
    assert "setInterval" not in BASE


def test_existing_event_channel_wakes_bell_on_every_shared_base_page():
    assert "bt38NotificationBell" in SIGNAL
    assert 'window.dispatchEvent(new CustomEvent("bt38-marketplace-event"' in SIGNAL
    assert 'window.addEventListener("bt38-marketplace-event"' in BASE
    dispatch_at = SIGNAL.index(
        'window.dispatchEvent(new CustomEvent("bt38-marketplace-event"'
    )
    product_linking_at = SIGNAL.index(
        'if (currentPath() === "/product-linking"'
    )
    assert dispatch_at < product_linking_at
