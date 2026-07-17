import ast
from pathlib import Path


PROTECTED_FIELDS = {
    "warehouse_stock_id",
    "master_product_group_id",
    "is_group_controlled",
}


def _target_function():
    source_path = Path(
        "governed_group_propagation_routes.py"
    )
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    for node in tree.body:
        if (
            isinstance(node, ast.FunctionDef)
            and node.name
            == "governed_group_propagate_quantity"
        ):
            return node

    raise AssertionError(
        "governed_group_propagate_quantity was not found"
    )


def test_quantity_propagation_does_not_assign_relationship_fields():
    function = _target_function()
    violations = []

    for node in ast.walk(function):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(
            node,
            (ast.AnnAssign, ast.AugAssign),
        ):
            targets = [node.target]
        else:
            continue

        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and target.attr in PROTECTED_FIELDS
            ):
                violations.append(
                    (target.attr, node.lineno)
                )

    assert violations == [], (
        "Quantity propagation must not assign protected "
        f"relationship fields: {violations}"
    )


def test_quantity_propagation_contains_relationship_validation():
    source = Path(
        "governed_group_propagation_routes.py"
    ).read_text(encoding="utf-8")

    required_fragments = (
        "saved_listing_group_id",
        "saved_stock_group_id",
        "saved_group_controlled",
        "marketplace listing relationships",
        "Warehouse",
        "group relationships",
        "Product Linking first",
    )

    missing = [
        fragment
        for fragment in required_fragments
        if fragment not in source
    ]

    assert missing == [], (
        "Quantity propagation relationship validation is incomplete: "
        f"{missing}"
    )
