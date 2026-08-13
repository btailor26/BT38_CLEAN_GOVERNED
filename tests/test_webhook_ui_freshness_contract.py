from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _signal_source():
    return (
        ROOT / "services" / "governed_ui_event_signal.py"
    ).read_text(encoding="utf-8")


def test_webhook_ui_freshness_is_event_driven_not_db_polled():
    main_source = (ROOT / "main.py").read_text(encoding="utf-8")
    base_source = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
    source = _signal_source()

    assert 'import services.governed_ui_event_signal' in main_source
    assert '/governed/ui/webhook-revision' not in main_source
    assert 'setInterval(checkWebhookRevision, 2000)' not in base_source
    assert 'MAX(completed_at)' not in source
    assert 'db.session' not in source
    assert '@app.get("/governed/ui/events")' in source
    assert 'threading.Condition()' in source
    assert '_condition.wait(timeout=25.0)' in source
    assert 'window.location.reload()' not in source


def test_webhook_ui_handoff_preserves_every_unseen_exact_record():
    source = _signal_source()

    assert '_events = deque(maxlen=256)' in source
    assert '_latest_event' not in source
    assert 'affected_listing_ids' in source
    assert 'affected_warehouse_stock_ids' in source
    assert 'affected_group_ids' in source
    assert 'unseen = _events_after(seen_revision)' in source
    assert '_collapse_events(unseen)' in source


def test_product_linking_consumes_full_webhook_mutation_contract():
    source = _signal_source()

    assert 'window.bt38ApplyProductLinkingMutation' in source
    assert 'await window.bt38ApplyProductLinkingMutation(contract, identity)' in source
    assert 'exactDetails(contract)' in source
    assert 'X-BT38-UI-Refresh' in source
    assert 'window.setTimeout(waitForNextEvent, 50)' in source


def test_completed_webhook_wakes_only_after_committed_change():
    source = _signal_source()

    assert 'publish_webhook_ui_event' in source
    assert '_response_has_committed_change(payload)' in source
    assert 'and committed_change' in source
    assert '_condition.notify_all()' in source

    # UI signalling must never become stock/push authority.
    assert 'push_marketplace' not in source
    assert 'push_group' not in source
    assert 'submit_governed_marketplace_action' not in source


def test_committed_product_link_is_published_on_existing_ui_event_channel():
    source = _signal_source()

    assert '"/governed/product-linking/link-listing-to-warehouse"' in source
    assert '"product_linking_link"' in source
    assert "publish_governed_ui_event(" in source
    assert "_result_has_committed_change(payload)" in source
    assert "response.status_code < 400" in source


def test_live_ui_scope_covers_fba_fbm_and_marketplace_pages():
    source = _signal_source()

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
