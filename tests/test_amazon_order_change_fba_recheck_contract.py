from pathlib import Path
import ast


CAPTURE_SOURCE = Path("services/governed_webhook_capture.py").read_text(
    encoding="utf-8"
)
EXECUTION_SOURCE = Path("services/governed_webhook_execution.py").read_text(
    encoding="utf-8"
)
ORDER_MUTATION_SOURCE = Path("services/governed_order_stock_mutation.py").read_text(
    encoding="utf-8"
)
ROUTES_SOURCE = Path("governed_routes.py").read_text(encoding="utf-8")

CAPTURE_TREE = ast.parse(CAPTURE_SOURCE)
EXECUTION_TREE = ast.parse(EXECUTION_SOURCE)
ORDER_MUTATION_TREE = ast.parse(ORDER_MUTATION_SOURCE)


def _function_source(tree: ast.AST, source: str, name: str) -> str:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"Function not found: {name}")


def test_direct_sqs_amazon_body_is_used_as_capture_payload():
    capture = _function_source(
        CAPTURE_TREE,
        CAPTURE_SOURCE,
        "capture_amazon_notification",
    )

    assert "message is None and isinstance(envelope, dict)" in capture
    assert "payload = envelope" in capture
    assert '("NotificationType",)' in capture
    assert '("NotificationMetadata", "NotificationId")' in capture


def test_capitalized_amazon_notification_type_is_recognized():
    event_type = _function_source(
        EXECUTION_TREE,
        EXECUTION_SOURCE,
        "_event_type",
    )

    assert 'payload.get("NotificationType")' in event_type


def test_fba_order_change_enters_canonical_order_intake_before_returning():
    process = _function_source(
        EXECUTION_TREE,
        EXECUTION_SOURCE,
        "process_marketplace_notification",
    )

    order_pos = process.index("_import_marketplace_order_from_notification(")
    mutation_pos = process.index("process_exact_marketplace_order_line(")
    fba_return_pos = process.index('status="fba_order_processed"')

    assert order_pos < mutation_pos < fba_return_pos
    assert 'status="fba_pending_stored"' not in process
    assert "fba_inventory_verification_required=True" in process


def test_fba_order_processor_records_order_without_stock_mutation():
    processor = _function_source(
        ORDER_MUTATION_TREE,
        ORDER_MUTATION_SOURCE,
        "process_exact_marketplace_order_line",
    )

    assert 'fulfillment in {"FBA", "AFN"}' in processor
    assert '"stock_mutated": False' in processor
    assert '"inventory_authority": "AmazonFBAInventory"' in processor


def test_fba_order_returns_before_warehouse_or_group_push():
    process = _function_source(
        EXECUTION_TREE,
        EXECUTION_SOURCE,
        "process_marketplace_notification",
    )

    fba_guard_pos = process.index("if is_amazon_fba:")
    fba_return_pos = process.index('status="fba_order_processed"', fba_guard_pos)
    group_push_pos = process.index("push_group_listings(", fba_return_pos)
    listing_push_pos = process.index("push_marketplace_listing(", fba_return_pos)

    assert fba_guard_pos < fba_return_pos < group_push_pos
    assert fba_guard_pos < fba_return_pos < listing_push_pos
    fba_section = process[fba_guard_pos:group_push_pos]
    assert "push_started=False" in fba_section
    assert "stock_changed=False" in fba_section


def test_existing_route_hands_fba_verification_to_memory_queue():
    assert "exact_fba_scope = bool(" in ROUTES_SOURCE
    assert "immediate_fba_result = _verify_exact_fba(" not in ROUTES_SOURCE
    assert "verification_queue_result = notify_governed_runtime_work(" in ROUTES_SOURCE
    assert 'source=f"webhook_{platform}_settlement_recheck"' in ROUTES_SOURCE
    assert "timedelta(seconds=90)" in ROUTES_SOURCE


def test_order_quantity_is_never_used_as_fba_inventory_truth():
    process = _function_source(
        EXECUTION_TREE,
        EXECUTION_SOURCE,
        "process_marketplace_notification",
    )

    assert "Amazon remains FBA inventory authority" in process
    assert "fba_inventory_verification_required=True" in process
    assert "Order quantity did not mutate Warehouse or FBA inventory" in process
