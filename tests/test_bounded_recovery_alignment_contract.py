from pathlib import Path


RECOVERY = Path("services/governed_recovery_alignment.py").read_text(
    encoding="utf-8"
)
MAIN = Path("main.py").read_text(encoding="utf-8")
EBAY_LISTING_RECOVERY = Path(
    "services/governed_ebay_missed_listing_recovery.py"
).read_text(encoding="utf-8")


def test_stranded_webhook_recovery_is_bounded_and_reuses_existing_execution():
    assert "def recover_stranded_ebay_notifications" in RECOVERY
    assert "limit: int = 25" in RECOVERY
    assert "max_age_hours: int = 48" in RECOVERY
    assert "completed_at IS NULL" in RECOVERY
    assert "received_at <= NOW() - INTERVAL '2 minutes'" in RECOVERY
    assert "process_marketplace_notification(" in RECOVERY
    assert "mark_notification_status(" in RECOVERY
    assert "MarketplaceOrder(" not in RECOVERY


def test_processed_ebay_mcf_recovery_is_bounded_and_reuses_existing_authorities():
    assert "def recover_processed_ebay_orders_for_mcf" in RECOVERY
    assert "limit: int = 10" in RECOVERY
    assert "max_age_hours: int = 72" in RECOVERY
    assert "MarketplaceOrder.processed_at.isnot(None)" in RECOVERY
    assert "MarketplaceOrder.mcf_order_id.is_(None)" in RECOVERY
    assert "MarketplaceOrder.shipped_at.is_(None)" in RECOVERY
    assert 'Store.platform.ilike("%ebay%")' in RECOVERY
    assert "hydrate_exact_ebay_order(" in RECOVERY
    assert "run_governed_mcf_submission(" in RECOVERY
    assert "auto_release=True" in RECOVERY
    assert "form_data={}" in RECOVERY
    assert "MCFOrder(" not in RECOVERY
    assert "create_fulfillment_order(" not in RECOVERY


def test_tracking_recovery_selects_only_dispatched_missing_tracking_mcf():
    assert "def recover_tracking_pending_mcf" in RECOVERY
    assert "limit: int = 10" in RECOVERY
    assert "MCFOrder.tracking_number.is_(None)" in RECOVERY
    assert "MarketplaceOrder.shipped_at.isnot(None)" in RECOVERY
    assert "refresh_mcf_from_amazon_signal" in RECOVERY
    assert '"sellerFulfillmentOrderId": seller_id' in RECOVERY
    assert "complete_sale(" not in RECOVERY


def test_group_recovery_reuses_existing_permanent_group_authority():
    assert "def recover_missing_original_groups" in RECOVERY
    assert "limit: int = 50" in RECOVERY
    assert "WarehouseStock.master_product_group_id.is_(None)" in RECOVERY
    assert "ensure_permanent_original_group" in RECOVERY
    assert "if listing.master_product_group_id is None:" in RECOVERY
    assert "MasterProductGroup(" not in RECOVERY


def test_recovery_alignment_creates_no_parallel_runtime():
    forbidden = (
        "Thread(",
        "Queue(",
        "start_worker(",
        "enqueue_sync_job(",
        "create_fulfillment_order(",
        "get_fulfillment_order(",
        "cancel_fulfillment_order(",
    )
    for fragment in forbidden:
        assert fragment not in RECOVERY

    assert '"full_scan_started": False' in RECOVERY
    assert '"new_worker_started": False' in RECOVERY
    assert '"new_queue_created": False' in RECOVERY


def test_ebay_listing_recovery_detects_changed_variation_structure_without_full_scan():
    assert "candidate_skus" in EBAY_LISTING_RECOVERY
    assert "existing_skus" in EBAY_LISTING_RECOVERY
    assert "changed_ids" in EBAY_LISTING_RECOVERY
    assert "candidate_skus.get(item_id) != existing_skus.get(item_id, set())" in EBAY_LISTING_RECOVERY
    assert "recovery_ids = list(dict.fromkeys([*missing_ids, *changed_ids]))" in EBAY_LISTING_RECOVERY
    assert "counts = _import_item(store, creds, candidates[item_id])" in EBAY_LISTING_RECOVERY
    assert '"full_catalogue_scan": False' in EBAY_LISTING_RECOVERY


def test_deployed_entrypoint_runs_bounded_recovery_once():
    assert "run_bounded_startup_recovery_alignment" in MAIN
    assert "BT38 bounded recovery alignment" in MAIN


def test_mcf_tracking_refresh_preserves_original_amazon_acceptance_clock():
    assert "original_mcf_status_refresh" in MAIN
    assert "accepted_at_before_refresh" in MAIN
    assert "mcf_order.amazon_status_updated_at = accepted_at_before_refresh" in MAIN
    assert "mcf_execution.refresh_mcf_status =" in MAIN
