from pathlib import Path
import ast

ROUTES = Path("governed_routes.py").read_text(encoding="utf-8")
GROUPS = Path("governed_group_routes.py").read_text(encoding="utf-8")


def function_source(source, name):
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node) or ""

    raise AssertionError(f"Function not found: {name}")


def test_empty_original_group_is_suppressed_only_while_member_is_shared_elsewhere():
    block = function_source(
        ROUTES,
        "governed_product_linking_data_compat",
    )

    assert "members_temporarily_shared_elsewhere = any(" in block
    assert "not current_group_listings" in block
    assert "and members_temporarily_shared_elsewhere" in block

    # Permanent Warehouse identity is only inspected, never cleared.
    assert "stock.master_product_group_id = None" not in block
    assert "listing.warehouse_stock_id = None" not in block


def test_master_is_defined_by_permanent_group_matching_current_group():
    block = function_source(
        GROUPS,
        "governed_group_unlink",
    )

    assert "permanent_group_id" in block
    assert "listing.warehouse_stock.master_product_group_id" in block
    assert "permanent_group_id == int(group_id)" in block


def test_master_unlink_is_blocked_before_relationship_mutation():
    block = function_source(
        GROUPS,
        "governed_group_unlink",
    )

    guard = block.index("if permanent_group_id == int(group_id):")
    mutation = block.index("listing.master_product_group_id = None")

    assert guard < mutation

    assert "master_listing=True" in block
    assert "master_sku=listing.external_sku" in block
    assert "unlinkable_skus=unlinkable_skus" in block
    assert "cannot be unlinked" in block


def test_master_block_lists_only_members_whose_permanent_group_differs():
    block = function_source(
        GROUPS,
        "governed_group_unlink",
    )

    # Current shared membership is not the unlinkability authority.
    # Permanent/original Warehouse group is.
    assert "member.warehouse_stock" in block
    assert "member_stock.master_product_group_id" in block
    assert "member_original_group_id != int(group_id)" in block
    assert "unlinkable_skus.append(sku)" in block
