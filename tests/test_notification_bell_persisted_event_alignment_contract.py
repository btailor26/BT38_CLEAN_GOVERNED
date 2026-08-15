from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROUTES = (ROOT / "governed_routes.py").read_text(encoding="utf-8")
BASE = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")


def test_bell_reads_existing_persisted_marketplace_events():
    assert '/governed/ui/notifications' in ROUTES
    assert 'MarketplaceOrder.query' in ROUTES
    assert 'SystemLog.log_type == "marketplace_webhook"' in ROUTES
    assert 'SystemLog.message.ilike("%listing%")' in ROUTES


def test_bell_hydrates_once_when_page_opens():
    block = BASE[
        BASE.index('<script id="bt38NotificationPanelScript">'):
        BASE.index('</script>', BASE.index('<script id="bt38NotificationPanelScript">'))
    ]

    assert '"/governed/ui/notifications?limit=20"' in block
    assert 'loadNotifications();' in block

    # Event-driven contract: no repeated browser polling.
    assert 'setInterval(' not in block
    assert 'setTimeout(' not in block


def test_unseen_state_survives_until_bell_is_checked():
    assert 'bt38.notifications.lastSeenAt' in BASE
    assert 'bt38.notifications.eventPending' in BASE
    assert 'setBellLight(unread > 0)' in BASE
    assert 'markSeen();' in BASE
    assert 'window.localStorage.setItem(seenKey' in BASE


def test_bell_is_display_only():
    # The bell read endpoint must not create marketplace/order/listing truth.
    start = ROUTES.index('def governed_ui_notifications():')
    end = ROUTES.find('\\n@governed_bp.', start + 10)
    block = ROUTES[start:end if end != -1 else None]

    forbidden = (
        'MarketplaceOrder(',
        'MarketplaceListing(',
        'WarehouseStock(',
        'db.session.add(',
        'db.session.commit(',
        'db.session.delete(',
    )

    for token in forbidden:
        assert token not in block
