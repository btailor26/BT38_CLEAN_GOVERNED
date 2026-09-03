from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NOTIFICATIONS = (ROOT / "services" / "governed_notification_read_alignment.py").read_text(encoding="utf-8")
DISPATCH = (ROOT / "services" / "governed_fbm_dispatch_queue_alignment.py").read_text(encoding="utf-8")
JOURNEY = (ROOT / "static" / "js" / "fbm_tracking_journey.js").read_text(encoding="utf-8")


def test_every_persisted_fbm_shipment_handoff_is_available_to_the_existing_bell():
    assert 'from fbm_models import FBMShipment' in NOTIFICATIONS
    for field in (
        '"label_assigned", "Label assigned / dispatched", "label_purchased_at"',
        '"marketplace_dispatch_confirmed", "Marketplace dispatch confirmed", "marketplace_confirmed_at"',
        '"carrier_accepted", "Picked up by carrier", "carrier_accepted_at"',
        '"in_transit", "In transit", "first_movement_at"',
        '"delivered", "Delivered", "delivered_at"',
    ):
        assert field in NOTIFICATIONS
    assert '"event_key": f"shipment:{shipment.id}:{event_type}:{changed_at.isoformat()}"' in NOTIFICATIONS
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


def test_committed_fbm_event_refreshes_the_existing_browser_session_without_reload_or_polling():
    assert 'fetch(window.location.href' in JOURNEY
    assert "headers: {'Accept': 'text/html'}" in JOURNEY
    assert 'BT38FBMApplyCommittedSnapshot' in JOURNEY
    assert 'window.location.reload()' not in JOURNEY
    assert 'new EventSource(' not in JOURNEY
    assert 'setInterval(' not in JOURNEY
    assert "window.addEventListener('bt38-marketplace-event', refreshFbmFromGovernedEvent)" in JOURNEY
