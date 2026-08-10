from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_webhook_ui_freshness_is_event_driven_not_db_polled():
    main_source = (ROOT / "main.py").read_text(encoding="utf-8")
    base_source = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
    signal_source = (
        ROOT / "services" / "governed_ui_event_signal.py"
    ).read_text(encoding="utf-8")

    assert 'import services.governed_ui_event_signal' in main_source
    assert '/governed/ui/webhook-revision' not in main_source
    assert 'setInterval(checkWebhookRevision, 2000)' not in base_source
    assert 'MAX(completed_at)' not in signal_source
    assert 'db.session' not in signal_source
    assert '@app.get("/governed/ui/events")' in signal_source
    assert 'threading.Condition()' in signal_source
    assert '_condition.wait(timeout=60.0)' in signal_source


def test_completed_webhook_wakes_live_ui_without_marketplace_or_stock_work():
    source = (
        ROOT / "services" / "governed_ui_event_signal.py"
    ).read_text(encoding="utf-8")

    assert 'publish_webhook_ui_event' in source
    assert 'notification_record_id' in source
    assert 'event: bt38-update' in source
    assert 'new EventSource("/governed/ui/events"' in source
    assert 'window.location.reload()' in source

    assert 'push_marketplace' not in source
    assert 'push_group' not in source
    assert 'sync' not in source.lower()


def test_live_ui_scope_covers_fba_fbm_and_marketplace_pages():
    source = (
        ROOT / "services" / "governed_ui_event_signal.py"
    ).read_text(encoding="utf-8")

    for path in (
        '"/warehouse"',
        '"/product-linking"',
        '"/amazon-fba-stock"',
        '"/listings"',
        '"/orders-mcf"',
    ):
        assert path in source

    assert '"/governed/webhooks/amazon"' in source
    assert '"/governed/webhooks/ebay"' in source
