from pathlib import Path


RECOVERY = Path(
    "services/governed_marketplace_dispatch_truth_recovery.py"
).read_text(encoding="utf-8")
AMAZON_TRACKING = Path(
    "services/governed_amazon_tracking_readback.py"
).read_text(encoding="utf-8")
DEPLOY = Path(".github/workflows/deploy-fly.yml").read_text(encoding="utf-8")


def test_recovery_selects_stale_lifecycle_not_missing_tracking():
    selector = RECOVERY.split("def _candidate_order_ids(", 1)[1].split(
        "def _recover_store(", 1
    )[0]
    assert 'MarketplaceOrder.fulfillment_type == "FBM"' in selector
    assert "MarketplaceOrder.created_at >= cutoff" not in selector
    assert "historical age must not exclude" in selector
    assert "_DISPATCHED_STATUSES" in RECOVERY
    assert "_PROTECTED_ISSUE_STATUSES" in RECOVERY
    assert "tracking_number" not in selector
    assert "carrier" not in selector


def test_recovery_reuses_exact_marketplace_readbacks_for_both_markets():
    assert "hydrate_exact_ebay_order(" in RECOVERY
    assert "hydrate_amazon_tracking_for_order(" in RECOVERY
    assert 'store_ids: tuple[int, ...] = (22, 23)' in RECOVERY
    assert "max_days: int = 90" in RECOVERY
    assert "limit_per_store: int = 150" in RECOVERY


def test_amazon_v2026_fulfillment_status_is_dispatch_authority():
    assert "def _order_fulfillment_status(" in AMAZON_TRACKING
    assert 'fulfillment = order_payload.get("fulfillment")' in AMAZON_TRACKING
    assert 'fulfillment.get("status")' in AMAZON_TRACKING
    assert "_order_fulfillment_status(order_payload)" in AMAZON_TRACKING
    assert '"SHIPPED": "shipped"' in AMAZON_TRACKING
    assert '"DELIVERED": "delivered"' in AMAZON_TRACKING
    assert '"order_status": _order_fulfillment_status(order_payload)' in AMAZON_TRACKING


def test_recovery_never_replays_orders_or_writes_marketplaces():
    assert '"marketplace_write_started": False' in RECOVERY
    assert '"stock_mutation_started": False' in RECOVERY
    assert '"order_replayed": False' in RECOVERY
    assert '"polling_started": False' in RECOVERY
    assert '"scheduler_started": False' in RECOVERY
    assert "process_marketplace_notification" not in RECOVERY
    assert "process_exact_marketplace_order_line" not in RECOVERY
    assert "requests.post(" not in RECOVERY
    assert "requests.put(" not in RECOVERY
    assert "requests.patch(" not in RECOVERY
    assert "requests.delete(" not in RECOVERY


def test_governed_deploy_does_not_run_cross_market_dispatch_recovery():
    assert "Recover stale marketplace dispatch truth once" not in DEPLOY
    assert "recover_bounded_marketplace_dispatch_truth" not in DEPLOY
