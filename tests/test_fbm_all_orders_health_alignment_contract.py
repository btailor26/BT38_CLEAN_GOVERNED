from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HEALTH = (ROOT / "services" / "governed_fbm_all_orders_health_alignment.py").read_text(encoding="utf-8")
CLARITY = (ROOT / "services" / "governed_order_clarity_alignment.py").read_text(encoding="utf-8")


def test_fbm_health_is_all_orders_not_date_scoped():
    assert "install_governed_fbm_all_orders_health_alignment" in CLARITY
    assert '"period_mode": "all"' in HEALTH
    assert '"period_label": "All FBM orders"' in HEALTH
    assert "No date filter hides actionable orders." in HEALTH
    assert "MarketplaceOrder.created_at >=" not in HEALTH
    assert "MarketplaceOrder.updated_at >=" not in HEALTH
    assert "health_period" not in HEALTH
    assert "health_date" not in HEALTH
    assert "health_month" not in HEALTH


def test_fbm_health_reads_latest_persisted_row_for_every_order_identity():
    assert "func.max(MarketplaceOrder.id)" in HEALTH
    assert ".group_by(MarketplaceOrder.store_id, MarketplaceOrder.marketplace_order_id)" in HEALTH
    assert ".join(latest_ids, MarketplaceOrder.id == latest_ids.c.id)" in HEALTH
    assert ".all()" in HEALTH
    assert ".limit(" not in HEALTH
    assert "Ready for FBM routing" in HEALTH
    assert 'route_state in {"Dispatched", "Tracking recorded"}' in HEALTH
    assert "awaiting_carrier_acceptance" in HEALTH
    assert "acceptance_overdue" in HEALTH


def test_all_orders_health_remains_db_only_and_preserves_fbm_guards():
    assert "_workspace_fbm_eligible" in HEALTH
    assert '"FBA", "AFN", "MCF"' in HEALTH
    assert "requests." not in HEALTH
    assert "db.session.add" not in HEALTH
    assert "db.session.commit" not in HEALTH
    assert "get_or_refresh_amazon_profile" not in HEALTH
