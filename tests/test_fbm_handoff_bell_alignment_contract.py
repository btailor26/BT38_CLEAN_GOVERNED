from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NOTIFICATIONS = (ROOT / "services" / "governed_notification_read_alignment.py").read_text(encoding="utf-8")
DISPATCH = (ROOT / "services" / "governed_fbm_dispatch_queue_alignment.py").read_text(encoding="utf-8")
JOURNEY = (ROOT / "static" / "js" / "fbm_tracking_journey.js").read_text(encoding="utf-8")
SESSION = (ROOT / "static" / "js" / "fbm_event_session_refresh_alignment.js").read_text(encoding="utf-8")
OVERLAY_PATH = ROOT / "services" / "governed_fbm_small_alignment.py"
OVERLAY = OVERLAY_PATH.read_text(encoding="utf-8")
MAIN = (ROOT / "main.py").read_text(encoding="utf-8")


def test_every_persisted_fbm_shipment_handoff_is_available_to_the_existing_bell():
    assert 'from fbm_models import FBMShipment' in NOTIFICATIONS
    for field in (
        '("label_assigned", "Label assigned / dispatched", "label_purchased_at")',
        '("marketplace_dispatch_confirmed", "Marketplace dispatch confirmed", "marketplace_confirmed_at")',
        '("carrier_accepted", "Picked up by carrier", "carrier_accepted_at")',
        '("in_transit", "In transit", "first_movement_at")',
        '("delivered", "Delivered", "delivered_at")',
    ):
        assert field in NOTIFICATIONS
    assert 'f"shipment:{shipment.id}:{event_type}:"' in NOTIFICATIONS
    assert 'f"{changed_at.isoformat()}"' in NOTIFICATIONS
    assert 'No marketplace call, sync,' in NOTIFICATIONS


def test_label_assignment_moves_only_original_outbound_dispatch_workflow():
    assert 'def _outbound_label_handoff_reached(shipment) -> bool:' in DISPATCH
    assert 'getattr(shipment, "label_purchased_at", None) is not None' in DISPATCH
    assert 'purchase_status == "purchased"' in DISPATCH
    assert '"packlink_return:"' in DISPATCH
    assert '"packlink_replacement:"' in DISPATCH
    classifier = DISPATCH.split('def _aligned_workflow_queue_for(row: MarketplaceOrder, shipment=None) -> str:', 1)[1].split('\ndef _health_route_state_from_marketplace_lifecycle', 1)[0]
    assert 'if reason:' in classifier
    assert 'if _outbound_label_handoff_reached(shipment):' in classifier
    assert classifier.index('if reason:') < classifier.index('if _outbound_label_handoff_reached(shipment):')


def test_committed_fbm_event_refreshes_once_and_the_session_sleeps_between_events():
    assert 'fetch(window.location.href' in JOURNEY
    assert "headers: {'Accept': 'text/html'}" in JOURNEY
    assert 'BT38FBMApplyCommittedSnapshot' in JOURNEY
    assert "window.addEventListener('bt38-marketplace-event', refreshFbmFromGovernedEvent)" in JOURNEY
    assert 'window.location.reload()' not in JOURNEY
    assert 'new EventSource(' not in JOURNEY
    assert 'setInterval(' not in JOURNEY

    assert "window.addEventListener('bt38-marketplace-event'" not in SESSION
    assert 'window.location.reload()' not in SESSION
    assert 'fetch(' not in SESSION
    assert 'new EventSource(' not in SESSION
    assert 'setInterval(' not in SESSION
    assert 'setTimeout(' not in SESSION
    assert 'MutationObserver' not in SESSION
    assert 'With no event, the FBM session sleeps.' in SESSION


def test_final_small_alignment_runs_after_existing_fbm_installers():
    compile(OVERLAY, str(OVERLAY_PATH), "exec")
    assert 'from services.governed_fbm_small_alignment import (' in MAIN
    assert 'install_governed_fbm_small_alignment(app)' in MAIN
    assert MAIN.index('install_governed_fbm_dispatch_queue_alignment(app)') < MAIN.rindex('install_governed_fbm_small_alignment(app)')
    assert MAIN.index('install_governed_notification_read_alignment(app)') < MAIN.rindex('install_governed_fbm_small_alignment(app)')


def test_pending_is_first_and_returns_are_separate_from_refunds():
    assert '"pending": "Pending"' in OVERLAY
    assert '"ready_dispatch": "Ready to dispatch"' in OVERLAY
    assert '"returns": "Returns"' in OVERLAY
    assert '"refunds": "Refunds"' in OVERLAY
    assert 'return "returns"' in OVERLAY
    assert 'return "refunds"' in OVERLAY
    assert "var sessionDefaults={tab:'pending',search:'',dirty:false};" in OVERLAY
    assert "addWorkflowButton(tabBar,'pending','Pending');\\n  addWorkflowButton(tabBar,'ready_dispatch','Ready to dispatch');" in OVERLAY
    assert "addWorkflowButton(tabBar,'returns','Returns');" in OVERLAY
    assert "getSessionState({tab: 'pending'" in SESSION
    assert "data-fbm-tab=\"pending\"" in SESSION


def test_final_browser_guard_can_only_hide_rows_outside_the_active_queue():
    assert 'function enforceActiveQueue()' in OVERLAY
    assert "if(String(row.dataset.fbmQueue||'')!==queue){row.hidden=true;row.style.display='none';}" in OVERLAY
    assert 'queueMicrotask(enforceActiveQueue)' in OVERLAY
    assert 'setInterval(' not in OVERLAY


def test_final_bell_reuses_existing_lifecycle_wrapper_and_keeps_only_business_webhook_activity():
    assert 'lifecycle._wrap_notification_bell(app)' in OVERLAY
    assert 'app._bt38_marketplace_bell_lifecycle_wrapped = False' in OVERLAY
    assert 'SystemLog.log_type == "marketplace_webhook"' in OVERLAY
    assert '"marketplace_notification"' in OVERLAY
    assert 'continue' in OVERLAY
    assert '"event_key": f"webhook:{log.id}"' in OVERLAY


def test_final_bell_deduplicates_commercial_sales_and_repeated_sync_outcomes_for_display_only():
    assert 'key = f"sale:{platform}:{order_id}:{sku}:{quantity}"' in OVERLAY
    assert '"marketplace_push_succeeded"' in OVERLAY
    assert '"marketplace_push_noop"' in OVERLAY
    assert 'key = f"sync:{log_type}:{platform}:{listing_id or sku}:{quantity}:{group_id}"' in OVERLAY
    assert 'DB history is untouched' in OVERLAY


def test_amazon_promise_is_persisted_only_during_existing_exact_read_and_rendered_in_london():
    assert 'original_fetch = amazon_profile._fetch_order' in OVERLAY
    assert 'payload.get("EarliestDeliveryDate")' in OVERLAY
    assert 'payload.get("LatestDeliveryDate")' in OVERLAY
    assert 'INSERT INTO fbm_order_operational_state' in OVERLAY
    assert 'ON CONFLICT (store_id, marketplace_order_id)' in OVERLAY
    assert 'COALESCE(EXCLUDED.latest_delivery_at, fbm_order_operational_state.latest_delivery_at)' in OVERLAY
    assert 'with db.session.begin_nested()' in OVERLAY
    assert 'ZoneInfo("Europe/London")' in OVERLAY
    assert 'promise_alignment._merge_promise = london_merge' in OVERLAY
    assert 'get_amazon_delivery_promise' not in OVERLAY


def test_saved_printer_is_restored_and_packlink_status_no_longer_forces_page_reload():
    assert 'bridge.savedPrinter()' in OVERLAY
    assert "status.textContent='Saved label printer: '+saved+' · Connect QZ to verify';" in OVERLAY
    assert "event.target.closest('.packlink-existing-status')" in OVERLAY
    assert "'/fbm/shipments/'+encodeURIComponent(shipmentId)+'/packlink/status'" in OVERLAY
    assert 'window.location.reload()' not in OVERLAY
    assert 'new EventSource(' not in OVERLAY
    assert 'setInterval(' not in OVERLAY
