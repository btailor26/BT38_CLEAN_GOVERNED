from pathlib import Path
import ast


PUSH_PATH = Path("services/governed_push_execution.py")
RUNTIME_PATH = Path("services/governed_runtime_engine.py")
ALIGNMENT_PATH = Path("services/governed_webhook_alignment.py")
WEBHOOK_PATH = Path("services/governed_webhook_execution.py")
GOVERNED_ROUTES_PATH = Path("governed_routes.py")
MAIN_PATH = Path("main.py")


def _source(path):
    return path.read_text(encoding="utf-8")


def _function_source(path, name):
    source = _source(path)
    tree = ast.parse(source)
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == name
    )
    return ast.get_source_segment(source, function)


def test_webhook_uses_canonical_order_and_warehouse_handoff_path():
    source = _source(WEBHOOK_PATH)
    assert "push_group_listings(" in source
    assert "push_marketplace_listing(" in source
    assert "upsert_governed_marketplace_order_line(" in source
    assert "process_exact_marketplace_order_line(" in source
    assert "MarketplaceOrder(" not in source
    assert "db.session.add(order)" not in source

    # Group correction is handed to the shared Warehouse-controlled service
    # with the exact Warehouse row changed by this webhook/order event.
    assert "authority_warehouse_stock_id=stock.id" in source


def test_webhook_current_product_linking_group_wins_over_permanent_stock_group():
    block = _function_source(WEBHOOK_PATH, "_resolve_group_context")

    assert "listing_group_id" in block
    assert "stock_group_id" in block
    assert "group_id = listing_group_id or stock_group_id" in block
    assert "group_id = stock_group_id or listing_group_id" not in block
    assert '"authority": "current_listing_relationship_then_warehouse_identity"' in block


def test_webhook_sku_lookup_honours_resolved_store_identity():
    block = _function_source(WEBHOOK_PATH, "_find_listing")

    assert 'store_id = payload.get("_bt38_store_id")' in block
    assert "MarketplaceListing.store_id == int(store_id)" in block
    assert "MarketplaceListing.external_sku == str(value)" in block


def test_existing_governed_push_queues_exact_alignment_scope():
    source = _source(PUSH_PATH)
    assert "notify_governed_runtime_work(" in source
    assert 'listing_ids=[getattr(listing, "id", None)]' in source
    assert "warehouse_stock_id=getattr(stock, \"id\", None)" in source
    assert "_queue_exact_group_webhook_verifications(" in source
    assert 'value.startswith("webhook_")' in source


def test_15_minute_alignment_does_not_use_broad_imports_or_scans():
    source = _source(RUNTIME_PATH)
    tree = ast.parse(source)
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_run_light_reconcile_cycle"
    )
    function_source = ast.get_source_segment(source, function)

    assert "_verify_webhook_event" in function_source
    assert "run_governed_marketplace_order_import" not in function_source
    assert "mutate_recent_marketplace_order_lines" not in function_source
    assert "run_governed_marketplace_import_refresh" not in function_source
    assert '"warehouse_scan_started": False' in function_source


def test_alignment_checks_only_saved_stock_and_listing_ids():
    source = _source(ALIGNMENT_PATH)
    assert "db.session.get(WarehouseStock, int(warehouse_stock_id))" in source
    assert "MarketplaceListing.id.in_(listing_ids)" in source
    assert "last_push_quantity" in source
    assert "last_push_status" in source
    assert "push_group_listings(" in source
    assert "push_marketplace_listing(" in source
    assert '"full_scan_started": False' in source
    assert "MarketplaceListing.query.all" not in source
    assert "WarehouseStock.query.all" not in source


def test_ebay_challenge_handler_is_installed_on_deployed_entrypoint():
    source = _source(MAIN_PATH)
    assert "install_ebay_notification_challenge_handler(app)" in source


def test_ebay_processing_failure_is_acknowledged_only_after_capture():
    source = _source(MAIN_PATH)
    assert "def acknowledge_captured_ebay_webhook(response):" in source
    assert 'request.path.rstrip("/") != "/governed/webhooks/ebay"' in source
    assert 'payload.get("status") != "processing_failed"' in source
    assert 'payload.get("notification_record_id") is None' in source
    assert "response.status_code = 200" in source
    assert 'response.headers["X-BT38-Webhook-Capture"] = "stored"' in source


def test_ebay_capture_failure_is_not_forced_to_success():
    source = _source(MAIN_PATH)
    assert 'payload.get("status") != "processing_failed"' in source
    assert 'payload.get("notification_record_id") is None' in source
    assert '"capture_failed"' not in source


def test_current_post_path_preserves_store_resolution_and_governed_execution():
    source = _source(GOVERNED_ROUTES_PATH)
    assert "capture_ebay_notification(request)" in source
    assert "store = _bt38_match_webhook_store(platform, payload)" in source
    assert 'payload["_bt38_store_id"] = int(store.id)' in source
    assert "process_marketplace_notification(" in source

def test_amazon_afn_handoff_queues_before_exact_fba_verification():
    route = _function_source(
        GOVERNED_ROUTES_PATH,
        "governed_marketplace_webhook_intake",
    )

    assert "exact_fba_scope" in route
    assert "stock_changed\n                or exact_fba_scope" in route
    assert "_verify_exact_fba(" not in route
    assert (
        "verification_queue_result = notify_governed_runtime_work("
        in route
    )
    assert route.index(
        "verification_queue_result = notify_governed_runtime_work("
    ) < route.index('processing_status="COMPLETED"')
    assert 'timedelta(seconds=90)' in route

