from pathlib import Path
import ast


CAPTURE_SOURCE = Path("services/governed_webhook_capture.py").read_text(
    encoding="utf-8"
)
FBA_IMPORT_SOURCE = Path("services/governed_amazon_inventory_import.py").read_text(
    encoding="utf-8"
)
RUNTIME_SOURCE = Path("services/governed_runtime_engine.py").read_text(
    encoding="utf-8"
)

CAPTURE_TREE = ast.parse(CAPTURE_SOURCE)
FBA_IMPORT_TREE = ast.parse(FBA_IMPORT_SOURCE)
RUNTIME_TREE = ast.parse(RUNTIME_SOURCE)


def _function_source(tree: ast.AST, source: str, name: str) -> str:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"Function not found: {name}")


def test_amazon_order_change_requires_canonical_order_before_completed():
    guard = _function_source(
        CAPTURE_TREE,
        CAPTURE_SOURCE,
        "_assert_amazon_order_change_canonical_order",
    )
    marker = _function_source(
        CAPTURE_TREE,
        CAPTURE_SOURCE,
        "mark_notification_status",
    )

    assert "marketplace_orders" in guard
    assert "marketplace_order_id = :order_id" in guard
    assert "processing_status = 'FAILED'" in guard
    assert "canonical_order_missing_after_order_change" in guard
    assert "raise RuntimeError(error)" in guard
    assert "_assert_amazon_order_change_canonical_order" in marker
    assert 'str(processing_status or "").strip().upper() == "COMPLETED"' in marker


def test_fba_inventory_handoff_uses_current_listing_group_only():
    relationship = _function_source(
        FBA_IMPORT_TREE,
        FBA_IMPORT_SOURCE,
        "_listing_relationship_scope",
    )
    apply_row = _function_source(
        FBA_IMPORT_TREE,
        FBA_IMPORT_SOURCE,
        "_apply_inventory_row",
    )

    assert 'getattr(listing, "master_product_group_id", None)' in relationship
    assert "WarehouseStock" not in relationship
    assert "current_group_id is not None" in relationship
    assert '"group_authority": "MarketplaceListing.master_product_group_id"' in apply_row
    assert '"linked_warehouse_stock_id"' in apply_row


def test_ungrouped_fba_cannot_feed_runtime_permanent_group_fallback():
    relationship = _function_source(
        FBA_IMPORT_TREE,
        FBA_IMPORT_SOURCE,
        "_listing_relationship_scope",
    )
    verify_fba = _function_source(
        RUNTIME_TREE,
        RUNTIME_SOURCE,
        "_verify_exact_fba",
    )

    # The historical runtime fallback still exists for legacy callers, so the
    # current FBA writer must not expose a propagation warehouse_stock_id when
    # Product Linking has no current group. This makes the fallback unreachable
    # from the governed FBA webhook path.
    assert "propagation_warehouse_stock_id = (" in relationship
    assert "if current_group_id is not None" in relationship
    assert "if group_id is None and warehouse_stock_id is not None" in verify_fba
