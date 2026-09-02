from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HEALTH = (ROOT / "services" / "governed_fbm_all_orders_health_alignment.py").read_text(encoding="utf-8")
SEARCH = (ROOT / "services" / "governed_fbm_global_search_alignment.py").read_text(encoding="utf-8")
CLARITY = (ROOT / "services" / "governed_order_clarity_alignment.py").read_text(encoding="utf-8")


def test_fbm_health_uses_operational_dispatch_scope_not_historical_action_count():
    assert "install_governed_fbm_all_orders_health_alignment" in CLARITY
    assert '"period_mode": "operational"' in HEALTH
    assert '"period_label": "Current FBM work"' in HEALTH
    assert "dispatch_due" in HEALTH
    assert "dispatched orders remain in history" in HEALTH.lower()


def test_fbm_health_reuses_the_browser_session_snapshot_without_an_order_rescan():
    assert "global_search._session_snapshot_rows()" in HEALTH
    assert "global_search.workflow_queue_for(row, shipment)" in HEALTH
    assert 'queue == "ready_dispatch"' in HEALTH
    assert 'queue == "dispatched"' in HEALTH
    assert "awaiting_carrier_acceptance" in HEALTH
    assert "acceptance_overdue" in HEALTH
    assert "db.session.query(MarketplaceOrder)" not in HEALTH
    assert "func.max(MarketplaceOrder.id)" not in HEALTH
    assert ".group_by(MarketplaceOrder.store_id, MarketplaceOrder.marketplace_order_id)" not in HEALTH
    assert "_bt38_fbm_session_rows" in SEARCH


def test_operational_health_remains_db_read_only_and_preserves_fbm_guards_through_session_loader():
    assert "_workspace_fbm_eligible" in HEALTH
    assert '"FBA", "AFN", "MCF"' in SEARCH
    assert "requests." not in HEALTH
    assert "db.session.add" not in HEALTH
    assert "db.session.commit" not in HEALTH
    assert "get_or_refresh_amazon_profile" not in HEALTH


def test_dispatched_history_does_not_inflate_shipping_action_count():
    assert '"shipping_actions": dispatch_due + overdue' in HEALTH
    assert '"dispatched": dispatched' in HEALTH
    assert 'platform.casefold() == "amazon"' in HEALTH
    assert 'page_alignment._health_html = operational_health_html' in HEALTH
    assert 'html.replace(mapping_card, "")' in HEALTH
