from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SYNC = (ROOT / "services" / "governed_warehouse_sync.py").read_text(encoding="utf-8")
RECONCILE = (
    ROOT / "services" / "governed_listing_structure_reconcile.py"
).read_text(encoding="utf-8")


def test_warehouse_sync_refreshes_listing_structure_and_orders():
    assert "run_governed_listing_structure_reconcile" in SYNC
    assert "listing_structure_reconcile" in SYNC
    assert "run_governed_marketplace_order_import" in SYNC
    assert "pending_order_recovery" in SYNC


def test_sync_never_turns_marketplace_quantity_into_warehouse_truth():
    assert '"marketplace_push_started": False' in SYNC
    assert '"warehouse_quantity_changed_from_marketplace": False' in SYNC
    assert '"push_started": False' in RECONCILE
    assert '"warehouse_quantity_changed": False' in RECONCILE


def test_ebay_reuses_variation_importer_and_retires_only_stale_siblings():
    assert "run_governed_ebay_inventory_import" in RECONCILE
    assert "affected_listing_ids" in RECONCILE
    assert "MarketplaceListing.external_listing_id == parent_id" in RECONCILE
    assert "if int(sibling.id) in current_ids" in RECONCILE
    assert "sibling.is_active = False" in RECONCILE
    assert "warehouse_stock_id =" not in RECONCILE
    assert "master_product_group_id =" not in RECONCILE


def test_amazon_sync_reuses_existing_listing_subscription_and_refresh_paths():
    assert "ensure_governed_amazon_listing_notification_subscriptions" in RECONCILE
    assert "run_governed_amazon_listing_fulfillment_refresh" in RECONCILE
