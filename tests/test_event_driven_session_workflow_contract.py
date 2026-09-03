from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / "docs" / "EVENT_DRIVEN_SESSION_WORKFLOW.md").read_text(encoding="utf-8")
MASTER = (ROOT / "BT38_CONTROL" / "01_MASTER_RULES.md").read_text(encoding="utf-8")
AGENTS = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
SIGNAL = (ROOT / "services" / "governed_ui_event_signal.py").read_text(encoding="utf-8")
READY = (ROOT / "services" / "governed_fbm_ready_landing_alignment.py").read_text(encoding="utf-8")


def test_governance_locks_zero_polling_and_session_driven_exact_record_updates():
    required = (
        "Zero polling",
        "Zero routine rebuilds",
        "session-driven",
        "update only the exact affected record",
        "No event = no work",
        "No polling. No rebuild. No broad reread. No parallel path.",
    )
    for marker in required:
        assert marker.lower() in WORKFLOW.lower()

    assert "docs/EVENT_DRIVEN_SESSION_WORKFLOW.md" in MASTER
    assert "docs/EVENT_DRIVEN_SESSION_WORKFLOW.md" in AGENTS
    assert "exact affected record/projection" in MASTER
    assert "exact record update in current session" in AGENTS


def test_workflow_forbids_event_triggered_page_rebuild_and_session_loss():
    for forbidden_contract in (
        "reload the page",
        "fetch the current page HTML to reconstruct state",
        "rebuild the whole table",
        "rerun the initial page query",
        "discard browser-local search, page, tab, selection, scroll, filter or workflow state",
        "second SSE/EventSource",
    ):
        assert forbidden_contract.lower() in WORKFLOW.lower()


def test_existing_handoff_stays_signal_only_and_db_blind():
    stream = SIGNAL.split('def governed_ui_event_stream():', 1)[1].split('@event.listens_for(Session, "before_flush")', 1)[0]
    assert "No DB read." in stream
    assert "No marketplace call." in stream
    assert "No polling." in stream
    assert "db.session" not in stream
    assert ".query(" not in stream
    assert "MarketplaceOrder" not in stream
    assert "FBMShipment" not in stream


def test_notification_bell_contract_remains_zero_query():
    assert "_lean_bell_reader" not in READY
    assert "governed_in_memory_events" in READY
    assert '"db_query": False' in READY
    assert "event_signal._events" in READY

    bell_reader = READY.split("def _in_memory_bell_reader():", 1)[1].split("\ndef _align_browser_pressure_response", 1)[0]
    for forbidden in (
        "db.session",
        ".query(",
        "MarketplaceOrder",
        "MarketplaceListing",
        "FBMShipment",
        "WarehouseStock",
        "SystemLog",
        "SyncLog",
        "requests.",
    ):
        assert forbidden not in bell_reader


def test_current_alignment_cannot_reintroduce_polling_or_broad_fbm_event_reread():
    assert "setInterval(" not in READY
    assert "new EventSource(" not in READY
    assert "fetch(window.location.href" not in READY
    assert "document.documentElement.dataset.bt38FbmCommittedStateDirty='1'" in READY


def test_workflow_requires_same_page_and_cross_page_event_alignment():
    assert "Same-page actions" in WORKFLOW
    assert "Cross-page events" in WORKFLOW
    assert "canonical commit -> exact affected-record event/response -> exact session record update -> sleep" in MASTER
    assert "canonical action/event -> DB commit -> exact affected-record event/response -> existing handoff -> exact record update in current session -> sleep" in AGENTS
