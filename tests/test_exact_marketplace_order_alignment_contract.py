import ast
from pathlib import Path


IMPORT_PATH = Path("services/governed_marketplace_order_import.py")
MUTATION_PATH = Path("services/governed_order_stock_mutation.py")


def _function(path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))

    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node

    raise AssertionError(f"{name} not found in {path}")


def _source(path: Path, function_name: str) -> str:
    text = path.read_text(encoding="utf-8")
    node = _function(path, function_name)
    return ast.get_source_segment(text, node) or ""


def test_importer_hands_exact_row_directly_to_processor():
    source = IMPORT_PATH.read_text(encoding="utf-8")

    assert '"_order_row": order' in source
    assert "process_exact_marketplace_order_line" in source
    assert "_process_exact_imported_order" in source


def test_importer_does_not_call_pending_row_sweep():
    source = IMPORT_PATH.read_text(encoding="utf-8")

    assert "mutate_recent_marketplace_order_lines(" not in source


def test_exact_processor_does_not_query_marketplace_orders():
    source = _source(
        MUTATION_PATH,
        "process_exact_marketplace_order_line",
    )

    assert "MarketplaceOrder.query" not in source
    assert "mutate_recent_marketplace_order_lines" not in source


def test_fba_remains_read_only_inventory_authority():
    source = _source(
        MUTATION_PATH,
        "process_exact_marketplace_order_line",
    )

    assert '"FBA"' in source
    assert '"AFN"' in source
    assert '"AmazonFBAInventory"' in source
    assert "mutate_warehouse_stock_from_order_line" in source


def test_repeat_import_does_not_reopen_processed_order():
    source = _source(
        IMPORT_PATH,
        "upsert_governed_marketplace_order_line",
    )

    assert 'not getattr(order, "processed_at", None)' in source


def test_amazon_and_ebay_import_as_pending_intake():
    source = IMPORT_PATH.read_text(encoding="utf-8")

    assert 'status="pending"' in source
    assert 'status = "pending"' in source
