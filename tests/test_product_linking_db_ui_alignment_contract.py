from pathlib import Path
import ast

SOURCE = Path("governed_routes.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)

def function_source(name):
    for node in ast.walk(TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(SOURCE, node) or ""
    raise AssertionError(f"Function not found: {name}")

def test_product_linking_payload_exposes_listing_current_group():
    block = function_source("governed_product_linking_data_compat")

    assert '"master_product_group_id": listing.master_product_group_id' in block
    assert (
        '"master_product_group_id": (\\n'
        '                listing.warehouse_stock.master_product_group_id'
        not in block
    )

def test_current_group_membership_uses_marketplace_listing_group():
    block = function_source("governed_product_linking_data_compat")
    tree = ast.parse(block)

    current_group_read = False

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue

        if not any(
            isinstance(target, ast.Name)
            and target.id == "current_group_id"
            for target in node.targets
        ):
            continue

        value = node.value

        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "getattr"
            and len(value.args) >= 2
            and isinstance(value.args[0], ast.Name)
            and value.args[0].id == "listing"
            and isinstance(value.args[1], ast.Constant)
            and value.args[1].value == "master_product_group_id"
        ):
            current_group_read = True

    assert current_group_read, (
        "Product Linking membership must read "
        "MarketplaceListing.master_product_group_id."
    )

    assert "if current_group_id:" in block
    assert "listings_by_group.setdefault(" in block

def test_unlinked_listing_returns_to_permanent_warehouse_row():
    block = function_source("governed_product_linking_data_compat")

    assert "elif listing.warehouse_stock_id:" in block
    assert "listings_by_stock.setdefault(" in block

def test_read_model_does_not_mutate_permanent_identity():
    block = function_source("governed_product_linking_data_compat")

    assert "listing.warehouse_stock_id =" not in block
    assert "listing.master_product_group_id =" not in block
    assert "stock.master_product_group_id =" not in block
