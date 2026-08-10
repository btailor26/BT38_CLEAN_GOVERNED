from pathlib import Path
import ast

SOURCE = Path("services/governed_webhook_execution.py").read_text(encoding="utf-8")


def _function(name: str) -> str:
    tree = ast.parse(SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(SOURCE, node) or ""
    raise AssertionError(f"Function not found: {name}")


def test_listing_resolution_does_not_choose_inactive_duplicate_sku():
    fn = _function("_find_listing")
    assert "MarketplaceListing.is_active == True" in fn


def test_amazon_afn_order_change_is_classified_before_warehouse_link_guard():
    fn = _function("process_marketplace_notification")
    afn = fn.index('payload_fulfillment_type in {"AFN", "FBA", "AMAZON"}')
    unlinked = fn.index('status="unlinked"')
    assert afn < unlinked


def test_afn_order_change_remains_read_only_inventory_signal():
    fn = _function("process_marketplace_notification")
    assert 'event_type == "order_change"' in fn
    assert 'business_event="fba_pending"' in fn
    assert "Order quantity was not treated as FBA" in fn
    assert "stock_changed=False" in fn
