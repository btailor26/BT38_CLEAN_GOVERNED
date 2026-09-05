from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

READY = (
    ROOT / "services" / "governed_fbm_ready_landing_alignment.py"
).read_text(encoding="utf-8")

PROJECTION = (
    ROOT / "services" / "governed_bell_event_projection_alignment.py"
).read_text(encoding="utf-8")


def test_bell_is_reminder_not_authority_or_inbox():
    assert '"bell_authority": False' in READY
    assert '"marketplace_calls": False' in READY
    assert '"polling": False' in READY
    assert "Reminder resolution belongs to Warehouse/FBM authority, not the bell." in READY
    assert "setBadge(0);" not in READY


def test_fbm_outstanding_dispatch_reminder_comes_from_current_order_truth():
    assert 'MarketplaceOrder.fulfillment_type == "FBM"' in READY
    assert 'actionable_statuses = ("pending", "unshipped", "confirmed", "partially_shipped")' in READY
    assert '"Get ready to dispatch"' in READY
    assert "MarketplaceOrder.status.in_(actionable_statuses)" in READY
    assert '"requires_action": True' in READY


def test_fbm_journey_reminder_comes_from_existing_shipment_truth():
    assert 'from fbm_models import FBMShipment' in READY
    assert '("Delivered", "delivered_at")' in READY
    assert '("In transit", "first_movement_at")' in READY
    assert '("Picked up", "carrier_accepted_at")' in READY
    assert '("Shipped", "marketplace_confirmed_at")' in READY
    assert '"requires_action": False' in READY


def test_listing_added_reminder_comes_from_existing_listing_truth():
    assert "MarketplaceListing.created_at >= cutoff" in READY
    assert '"log_type": "marketplace_listing"' in READY
    assert '"Listing added ·' in READY
    assert '"requires_action": False' in READY


def test_badge_counts_only_outstanding_actions_not_all_movements():
    assert '"action_count": action_count' in READY
    assert 'record.get("requires_action") is True' in READY
    assert "records.filter(function(record) { return record && record.requires_action === true; }).length" in READY
    assert "const pending = records.length;" not in READY


def test_opening_bell_replaces_stale_browser_projection_not_authority():
    assert 'localStorage.removeItem("bt38.notifications.exactEventRecords.v2")' in READY
    assert 'panel.addEventListener("show.bs.offcanvas"' in READY
    assert "db.session.add(" not in READY
    assert "db.session.commit(" not in READY


def test_existing_projection_install_contract_is_preserved():
    assert "def _event_only_bell_reader():" in READY
    assert "ready._event_only_bell_reader" in PROJECTION


def test_fbm_bell_uses_product_title_and_existing_ship_by_presentation():
    assert "productTitle=text(row.querySelector('td:nth-child(4) strong'))" in PROJECTION
    assert "shipBy=text(row.querySelector('td:nth-child(7) .fbm-promise-line span'))" in PROJECTION
    assert "subject=productTitle||(orderId?'Order '+orderId:'Order')" in PROJECTION
    assert "parts.push('Ship by '+shipBy)" in PROJECTION
    assert "ship_by_at:shipBy" in PROJECTION
    assert "var detail=event&&event.detail||{},projected=fbmProjection(detail);" in PROJECTION


def test_no_rebuild_polling_or_historical_replay_is_added():
    lowered = READY.lower()
    assert "setinterval(" not in lowered
    assert "threading.thread" not in lowered
    assert "90 days" not in lowered
    assert "backfill" not in lowered
    assert "notification ledger" in lowered
