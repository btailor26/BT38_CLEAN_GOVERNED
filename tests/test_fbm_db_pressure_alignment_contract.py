from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALIGNMENT = (ROOT / "services" / "governed_fbm_ready_landing_alignment.py").read_text(encoding="utf-8")
TRACKING = (ROOT / "static" / "js" / "fbm_tracking_journey.js").read_text(encoding="utf-8")
BASE = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")


def test_committed_event_no_longer_hits_full_fbm_html_after_response_alignment():
    assert "window.addEventListener('bt38-marketplace-event', refreshFbmFromGovernedEvent);" in TRACKING
    assert "document.documentElement.dataset.bt38FbmCommittedStateDirty='1'" in ALIGNMENT
    assert "body.replace(" in ALIGNMENT
    assert "fetch(window.location.href" not in ALIGNMENT
    assert "setInterval(" not in ALIGNMENT
    assert "new EventSource(" not in ALIGNMENT


def test_bell_page_wake_is_neutralised_but_explicit_open_remains_owner():
    assert 'panel.addEventListener("show.bs.offcanvas"' in BASE
    assert "hydrateBellAfterWake();" in BASE
    assert 'html.replace("hydrateBellAfterWake();", "stale = true;")' in ALIGNMENT


def test_final_bell_reader_is_two_lean_business_queries_only():
    reader = ALIGNMENT.split("def _lean_bell_reader():", 1)[1].split("\ndef _align_browser_pressure_response", 1)[0]
    assert "MarketplaceOrder.id" in reader
    assert "Store.platform" in reader
    assert "WarehouseStock.product_name" in reader
    assert "FBMShipment.id" in reader
    assert "MarketplaceListing" not in reader
    assert "SyncLog" not in reader
    assert "SystemLog" not in reader
    assert "joinedload" not in reader
    assert "api_key" not in reader
    assert "ship_to_address" not in reader
    assert "marketplace_write" not in reader


def test_alignment_keeps_ready_to_dispatch_as_first_landing():
    assert "var sessionDefaults={tab:'ready_dispatch',search:'',dirty:false};" in ALIGNMENT
    assert "saved.tab!=='pending'" in ALIGNMENT
    assert "addWorkflowButton(tabBar,'ready_dispatch','Ready to dispatch');" in ALIGNMENT
