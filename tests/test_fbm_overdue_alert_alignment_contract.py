from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OVERDUE = (ROOT / "services" / "governed_fbm_overdue_alert_alignment.py").read_text(encoding="utf-8")
CLARITY = (ROOT / "services" / "governed_order_clarity_alignment.py").read_text(encoding="utf-8")


def test_overdue_alert_is_red_clickable_and_filters_to_overdue_only():
    assert "install_governed_fbm_overdue_alert_alignment" in CLARITY
    assert 'id="bt38FbmOverdueAlert"' in OVERDUE
    assert "bt38FbmOverduePulse" in OVERDUE
    assert 'href="{escape(href)}"' in OVERDUE
    assert 'health_filter=overdue' in OVERDUE
    assert "Show overdue orders" in OVERDUE
    assert "Showing {overdue} overdue FBM order" in OVERDUE
    assert "return _latest_overdue_rows(page_alignment), False" in OVERDUE


def test_overdue_filter_uses_persisted_latest_shipment_truth_only():
    assert "func.max(FBMShipment.id)" in OVERDUE
    assert ".group_by(FBMShipment.store_id, FBMShipment.marketplace_order_id)" in OVERDUE
    assert "FBMShipment.handover_due_at < datetime.utcnow()" in OVERDUE
    assert "FBMShipment.carrier_accepted_at.is_(None)" in OVERDUE
    assert "FBMShipment.delivered_at.is_(None)" in OVERDUE
    assert "func.max(MarketplaceOrder.id)" in OVERDUE
    assert "_workspace_fbm_eligible" in OVERDUE


def test_overdue_alignment_does_not_hammer_db_or_add_background_work():
    assert "_HEALTH_CACHE_TTL_SECONDS = 60.0" in OVERDUE
    assert "monotonic()" in OVERDUE
    assert "_cached_health_summary" in OVERDUE
    assert "now - cached_at < _HEALTH_CACHE_TTL_SECONDS" in OVERDUE
    assert "requests." not in OVERDUE
    assert "db.session.add" not in OVERDUE
    assert "db.session.commit" not in OVERDUE
    assert "setInterval" not in OVERDUE
    assert "setTimeout" not in OVERDUE
    assert "scheduler" not in OVERDUE.lower()
    assert "poll" not in OVERDUE.lower()
