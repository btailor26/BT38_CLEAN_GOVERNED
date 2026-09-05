from pathlib import Path


RECOVERY_PATH = Path("services/governed_marketplace_dispatch_truth_recovery.py")
AMAZON_TRACKING = Path(
    "services/governed_amazon_tracking_readback.py"
).read_text(encoding="utf-8")
DEPLOY = Path(".github/workflows/deploy-fly.yml").read_text(encoding="utf-8")


def test_broad_historical_dispatch_recovery_is_removed():
    assert not RECOVERY_PATH.exists()


def test_amazon_v2026_fulfillment_status_is_dispatch_authority():
    assert "def _order_fulfillment_status(" in AMAZON_TRACKING
    assert 'fulfillment = order_payload.get("fulfillment")' in AMAZON_TRACKING
    assert 'fulfillment.get("status")' in AMAZON_TRACKING
    assert "_order_fulfillment_status(order_payload)" in AMAZON_TRACKING
    assert '"SHIPPED": "shipped"' in AMAZON_TRACKING
    assert '"DELIVERED": "delivered"' in AMAZON_TRACKING
    assert '"order_status": _order_fulfillment_status(order_payload)' in AMAZON_TRACKING


def test_governed_deploy_does_not_run_cross_market_dispatch_recovery():
    assert "services/governed_marketplace_dispatch_truth_recovery.py" not in DEPLOY
    assert "Recover stale marketplace dispatch truth once" not in DEPLOY
    assert "recover_bounded_marketplace_dispatch_truth" not in DEPLOY
    assert "max_days=90" not in DEPLOY
    assert "limit_per_store=150" not in DEPLOY
