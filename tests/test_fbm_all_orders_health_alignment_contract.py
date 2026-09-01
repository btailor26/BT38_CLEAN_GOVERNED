from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HEALTH = (ROOT / "services" / "governed_fbm_all_orders_health_alignment.py").read_text(encoding="utf-8")
CLARITY = (ROOT / "services" / "governed_order_clarity_alignment.py").read_text(encoding="utf-8")


def test_fbm_health_uses_operational_dispatch_scope_not_historical_action_count():
    assert "install_governed_fbm_all_orders_health_alignment" in CLARITY
    assert '"period_mode": "operational"' in HEALTH
    assert '"period_label": "Current FBM work"' in HEALTH
    assert "dispatch_due" in HEALTH
    assert "dispatched orders remain in history" in HEALTH.lower()


def test_fbm_health_reads_latest_persisted_row_for_every_order_identity():
    assert "func.max(MarketplaceOrder.id)" in HEALTH
    assert ".group_by(MarketplaceOrder.store_id, MarketplaceOrder.marketplace_order_id)" in HEALTH
    assert ".join(latest_ids, MarketplaceOrder.id == latest_ids.c.id)" in HEALTH
    assert ".all()" in HEALTH
    assert "fulfillment_section" in HEALTH
    assert 'section.view == "dispatch_due"' in HEALTH
    assert 'section.view == "dispatched"' in HEALTH
    assert "awaiting_carrier_acceptance" in HEALTH
    assert "acceptance_overdue" in HEALTH


def test_operational_health_remains_db_only_and_preserves_fbm_guards():
    assert "_workspace_fbm_eligible" in HEALTH
    assert '"FBA", "AFN", "MCF"' in HEALTH
    assert "requests." not in HEALTH
    assert "db.session.add" not in HEALTH
    assert "db.session.commit" not in HEALTH
    assert "get_or_refresh_amazon_profile" not in HEALTH


def test_dispatched_history_does_not_inflate_shipping_action_count():
    assert '"shipping_actions": dispatch_due + overdue + mapping_review' in HEALTH
    assert '"dispatched": dispatched' in HEALTH
