from pathlib import Path
import ast


PUSH_PATH = Path("services/governed_push_execution.py")
RUNTIME_PATH = Path("services/governed_runtime_engine.py")
ALIGNMENT_PATH = Path("services/governed_webhook_alignment.py")
WEBHOOK_PATH = Path("services/governed_webhook_execution.py")


def _source(path):
    return path.read_text(encoding="utf-8")


def test_existing_webhook_execution_still_auto_pushes():
    source = _source(WEBHOOK_PATH)

    # Grouped notifications use the shared Warehouse authority path.
    assert "run_governed_group_propagation(" in source
    assert '"warehouse_stock_id": stock.id' in source
    assert "push_group_listings(" not in source

    # Ungrouped notifications retain the exact listing push path.
    assert "push_marketplace_listing(" in source

    assert "upsert_governed_marketplace_order_line(" in source
    assert "process_exact_marketplace_order_line(" in source
    assert "MarketplaceOrder(" not in source
    assert "db.session.add(order)" not in source


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
