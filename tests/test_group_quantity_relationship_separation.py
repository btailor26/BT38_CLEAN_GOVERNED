import ast
from pathlib import Path


PROTECTED_FIELDS = {
    "warehouse_stock_id",
    "master_product_group_id",
    "is_group_controlled",
}


def _function(source_path: str, name: str):
    source = Path(source_path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return source, node
    raise AssertionError(f"{name} was not found")


def test_quantity_propagation_route_is_thin_adapter():
    source, function = _function(
        "governed_group_propagation_routes.py",
        "run_governed_group_propagation",
    )
    block = ast.get_source_segment(source, function) or ""
    assert "push_group_listings(" in block
    assert "submit_governed_marketplace_action" not in block
    assert "MarketplaceListing" not in block
    assert "WarehouseStock" not in block


def test_shared_group_push_does_not_assign_relationship_fields():
    source, function = _function(
        "services/governed_push_execution.py",
        "push_group_listings",
    )
    violations = []
    for node in ast.walk(function):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        else:
            continue
        for target in targets:
            for child in ast.walk(target):
                if isinstance(child, ast.Attribute) and child.attr in PROTECTED_FIELDS:
                    violations.append((child.attr, node.lineno))
    assert violations == [], (
        "Group push may synchronize quantity but must never mutate relationship identity: "
        f"{violations}"
    )


def test_shared_group_push_uses_current_relationship_and_warehouse_quantity():
    source, function = _function(
        "services/governed_push_execution.py",
        "push_group_listings",
    )
    block = ast.get_source_segment(source, function) or ""
    assert "MarketplaceListing.master_product_group_id == group_id" in block
    assert "MarketplaceListing.warehouse_stock_id.isnot(None)" in block
    assert 'getattr(authority_stock, "sellable_quantity", 0)' in block
    assert "stock.available_quantity = int(target_quantity + reserved + allocated)" in block
    forbidden = (
        "listing.master_product_group_id =",
        "listing.warehouse_stock_id =",
        "stock.master_product_group_id =",
    )
    for value in forbidden:
        assert value not in block
