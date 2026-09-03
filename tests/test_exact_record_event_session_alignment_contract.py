from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALIGNMENT = (ROOT / "services" / "governed_exact_record_event_alignment.py").read_text(encoding="utf-8")
MAIN = (ROOT / "main.py").read_text(encoding="utf-8")
EVENTS = (ROOT / "services" / "governed_ui_event_signal.py").read_text(encoding="utf-8")
WAREHOUSE = (ROOT / "static" / "js" / "warehouse-governed.js").read_text(encoding="utf-8")
FBM_SESSION = (ROOT / "static" / "js" / "fbm_event_session_refresh_alignment.js").read_text(encoding="utf-8")


def test_exact_alignment_replaces_generic_commit_wake_not_adds_second_path():
    assert 'event.remove(Session, identifier, fn)' in ALIGNMENT
    assert 'ui._bt38_existing_ui_signal_before_flush' in ALIGNMENT
    assert 'ui._bt38_existing_ui_signal_after_commit' in ALIGNMENT
    assert 'event.listen(Session, "before_flush", _exact_before_flush)' in ALIGNMENT
    assert 'event.listen(Session, "after_commit", _exact_after_commit)' in ALIGNMENT


def test_commit_event_carries_exact_record_identity():
    for marker in (
        'affected_listing_ids',
        'affected_warehouse_stock_ids',
        'affected_group_ids',
        'order_id',
        'seller_sku',
        'store_id',
    ):
        assert marker in ALIGNMENT
    assert 'scope={\n                "event_type": "committed_marketplace_state"' in EVENTS
    assert 'install_governed_exact_record_event_alignment(app)' in MAIN


def test_sse_is_in_memory_and_transports_payload_without_db_or_api_reads():
    stream = ALIGNMENT.split('def _event_stream_response():', 1)[1].split('\ndef _align_base_event_payload', 1)[0]
    assert 'ui._condition' in stream
    assert 'ui._events_after(seen_revision)' in stream
    assert 'json.dumps(committed' in stream
    for forbidden in ('db.session', '.query(', 'MarketplaceOrder', 'FBMShipment', 'requests.', 'fetch('):
        assert forbidden not in stream


def test_browser_event_handoff_is_exact_and_never_rebuilds_page():
    assert 'JSON.parse(event.data || "{}")' in ALIGNMENT
    assert 'event: committedEvent' in ALIGNMENT
    assert "exactSku = String(detail?.seller_sku || '').trim()" in ALIGNMENT
    assert 'refreshProductLinkingSilently(detail || {})' in ALIGNMENT
    assert 'fetch(window.location.href' not in ALIGNMENT
    assert 'window.location.reload' not in ALIGNMENT
    assert 'setInterval(' not in ALIGNMENT
    assert 'new EventSource(' not in ALIGNMENT


def test_assistant_cannot_fetch_dashboard_on_event_or_page_wake():
    assert 'const cached = Number(window.sessionStorage.getItem(cacheKey));' in ALIGNMENT
    replacement = ALIGNMENT.split("async function readDashboardActionCount()", 1)[1].split("async function refreshAssistant", 1)[0]
    assert 'fetch(' not in replacement
    assert 'dashboardPath' not in replacement


def test_warehouse_remains_session_driven_without_reload_or_polling():
    assert 'NO RELOADS - NO FORM SUBMIT - GOVERNED ACTION ONLY' in WAREHOUSE
    assert 'setInterval(' not in WAREHOUSE
    assert 'window.location.reload' not in WAREHOUSE
    assert 'loadVisibleProfitability();' in WAREHOUSE  # initial visible-session enrichment only


def test_fbm_session_alignment_has_no_polling_or_second_transport():
    assert 'setInterval(' not in FBM_SESSION
    assert 'new EventSource(' not in FBM_SESSION
    assert 'MutationObserver' not in FBM_SESSION
    assert 'fetch(window.location.href' not in ALIGNMENT


def test_locked_architecture_is_event_then_exact_record_then_sleep():
    workflow = (ROOT / 'docs' / 'EVENT_DRIVEN_SESSION_WORKFLOW.md').read_text(encoding='utf-8')
    assert 'Zero polling. Zero routine rebuilds. Zero broad rereads after an event.' in workflow
    assert 'exact affected-record event' in workflow
    assert 'current browser session owns presentation state' in workflow
    assert 'No-rebuild contract' in workflow
    assert 'one committed event -> one exact affected projection update -> existing session preserved -> sleep' in workflow
