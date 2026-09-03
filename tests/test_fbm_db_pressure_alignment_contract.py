from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALIGNMENT = (ROOT / "services" / "governed_fbm_ready_landing_alignment.py").read_text(encoding="utf-8")
TRACKING = (ROOT / "static" / "js" / "fbm_tracking_journey.js").read_text(encoding="utf-8")
BASE = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
SIGNAL = (ROOT / "services" / "governed_ui_event_signal.py").read_text(encoding="utf-8")


def test_committed_event_no_longer_hits_full_fbm_html_after_response_alignment():
    assert "window.addEventListener('bt38-marketplace-event', refreshFbmFromGovernedEvent);" in TRACKING
    assert "document.documentElement.dataset.bt38FbmCommittedStateDirty='1'" in ALIGNMENT
    assert "body.replace(" in ALIGNMENT
    assert "fetch(window.location.href" not in ALIGNMENT
    assert "setInterval(" not in ALIGNMENT
    assert "new EventSource(" not in ALIGNMENT


def test_bell_page_wake_is_neutralised_and_explicit_open_cannot_hit_neon():
    assert 'panel.addEventListener("show.bs.offcanvas"' in BASE
    assert "hydrateBellAfterWake();" in BASE
    assert 'html.replace("hydrateBellAfterWake();", "stale = true;")' in ALIGNMENT
    assert '_event_only_bell_reader._bt38_zero_query_event_bell = True' in ALIGNMENT


def test_notification_bell_is_hard_locked_to_existing_in_memory_event_queue_only():
    reader = ALIGNMENT.split("def _event_only_bell_reader():", 1)[1].split("\ndef _align_ready_landing_html", 1)[0]

    assert "governed_ui_event_signal as event_signal" in reader
    assert "event_signal._condition" in reader
    assert "event_signal._events" in reader
    assert '"source": "governed_in_memory_events"' in reader
    assert '"db_query": False' in reader

    forbidden = (
        "db.session",
        ".query(",
        "MarketplaceOrder",
        "MarketplaceListing",
        "FBMShipment",
        "WarehouseStock",
        "SystemLog",
        "SyncLog",
        "joinedload",
        "select(",
        "run_sql",
        "marketplace_api",
        "requests.",
    )
    for token in forbidden:
        assert token not in reader


def test_zero_query_bell_contract_cannot_regress_to_a_lean_db_reader():
    assert "def _lean_bell_reader" not in ALIGNMENT
    assert "bell DB read" not in ALIGNMENT
    assert "MarketplaceOrder.id" not in ALIGNMENT
    assert "FBMShipment.id" not in ALIGNMENT
    assert "WarehouseStock.product_name" not in ALIGNMENT


def test_existing_ui_event_transport_remains_db_blind():
    assert "_events = deque(maxlen=256)" in SIGNAL
    assert 'No DB read.' in SIGNAL
    assert '_condition.wait(timeout=25.0)' in SIGNAL
    assert "db.session" not in SIGNAL.split('def governed_ui_event_stream():', 1)[1].split('@event.listens_for', 1)[0]


def test_alignment_keeps_ready_to_dispatch_as_first_landing():
    assert "var sessionDefaults={tab:'ready_dispatch',search:'',dirty:false};" in ALIGNMENT
    assert "saved.tab!=='pending'" in ALIGNMENT
    assert "addWorkflowButton(tabBar,'ready_dispatch','Ready to dispatch');" in ALIGNMENT
