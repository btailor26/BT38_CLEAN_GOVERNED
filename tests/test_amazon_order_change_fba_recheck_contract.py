from pathlib import Path
import ast


CAPTURE_SOURCE = Path("services/governed_webhook_capture.py").read_text(
    encoding="utf-8"
)
EXECUTION_SOURCE = Path("services/governed_webhook_execution.py").read_text(
    encoding="utf-8"
)
ROUTES_SOURCE = Path("governed_routes.py").read_text(encoding="utf-8")

CAPTURE_TREE = ast.parse(CAPTURE_SOURCE)
EXECUTION_TREE = ast.parse(EXECUTION_SOURCE)


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


def test_afn_order_change_exits_before_sale_quantity_path():
    process = _function_source(
        EXECUTION_TREE,
        EXECUTION_SOURCE,
        "process_marketplace_notification",
    )

    guard_pos = process.index('event_type == "order_change"')
    quantity_pos = process.index("quantity = _extract_quantity(payload)")
    order_pos = process.index("_import_marketplace_order_from_notification(")

    assert guard_pos < quantity_pos < order_pos
    assert 'payload_fulfillment_type in {"AFN", "FBA", "AMAZON"}' in process
    assert 'status="fba_pending_stored"' in process
    assert 'business_event="fba_pending"' in process
    assert "stock_changed=False" in process
    assert 'seller_sku=(' in process


def test_existing_route_remains_fba_verification_authority():
    assert "exact_fba_scope = bool(" in ROUTES_SOURCE
    assert "immediate_fba_result = _verify_exact_fba(" in ROUTES_SOURCE
    assert 'source=f"webhook_{platform}_settlement_recheck"' in ROUTES_SOURCE
    assert "timedelta(seconds=90)" in ROUTES_SOURCE


def test_order_change_does_not_apply_order_quantity_as_fba_inventory():
    process = _function_source(
        EXECUTION_TREE,
        EXECUTION_SOURCE,
        "process_marketplace_notification",
    )

    guard_start = process.index('event_type == "order_change"')
    quantity_start = process.index("quantity = _extract_quantity(payload)")
    guard_section = process[guard_start:quantity_start]

    assert "apply_governed_amazon_fba_event" not in guard_section
    assert "process_exact_marketplace_order_line" not in guard_section
    assert "push_group_listings" not in guard_section
