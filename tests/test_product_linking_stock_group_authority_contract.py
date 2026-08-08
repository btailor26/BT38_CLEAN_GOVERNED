import ast
from pathlib import Path


ROUTES = Path("governed_routes.py").read_text(encoding="utf-8")
PUSH = Path(
    "services/governed_push_execution.py"
).read_text(encoding="utf-8")
PROPAGATION = Path(
    "governed_group_propagation_routes.py"
).read_text(encoding="utf-8")


def _function_block(source: str, name: str) -> str:
    marker = f"def {name}("
    start = source.index(marker)
    next_function = source.find("\ndef ", start + len(marker))
    if next_function == -1:
        return source[start:]
    return source[start:next_function]


def test_product_linking_dataset_uses_two_role_relationship_authority():
    block = _function_block(
        ROUTES,
        "governed_product_linking_data_compat",
    )

    # Permanent Warehouse identity remains warehouse_stock_id.
    assert "MarketplaceListing.warehouse_stock_id.in_(" in block

    # Current Product Linking membership comes from MarketplaceListing.
    assert "current_group_id" in block
    assert "listing.master_product_group_id" in block
    assert "if current_group_id:" in block
    assert "listings_by_group.setdefault(" in block

    # An unlinked listing falls back to its permanent Warehouse row.
    assert "elif listing.warehouse_stock_id:" in block
    assert "listings_by_stock.setdefault(" in block

    # The old Warehouse-group-as-current-membership rule must not return.
    assert (
        'stock_group_id = (\n'
        '            getattr(stock, "master_product_group_id", None)'
        not in block
    )

def test_group_push_members_use_current_listing_relationship():
    block = _function_block(PUSH, "push_group_listings")

    assert (
        "MarketplaceListing.master_product_group_id == group_id"
        in block
    )
    assert (
        "MarketplaceListing.warehouse_stock_id.isnot(None)"
        in block
    )
    assert (
        "WarehouseStock.master_product_group_id == group_id"
        not in block
    )

    # Response/audit metadata may report the listings selected through
    # the governed current relationship.
    assert '"direct_group_listing_ids"' in block


def test_single_listing_automatic_group_expansion_has_no_listing_fallback():
    block = _function_block(PUSH, "push_marketplace_listing")
    tree = ast.parse(block)

    listing_group_read = False

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "listing"
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "master_product_group_id"
        ):
            listing_group_read = True

    assert listing_group_read, (
        "Automatic single-listing expansion must use the listing's "
        "current Product Linking group."
    )


def test_push_does_not_assign_relationship_fields():
    block = _function_block(PUSH, "push_group_listings")
    tree = ast.parse(block)
    relationship_fields = {
        "master_product_group_id",
        "warehouse_stock_id",
        "is_group_controlled",
    }

    assigned_relationship_fields = set()
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = (
                node.targets
                if isinstance(node, ast.Assign)
                else [node.target]
            )
        elif isinstance(node, ast.AugAssign):
            targets = [node.target]

        for target in targets:
            for child in ast.walk(target):
                if (
                    isinstance(child, ast.Attribute)
                    and child.attr in relationship_fields
                ):
                    assigned_relationship_fields.add(child.attr)

    assert assigned_relationship_fields == set()


def test_group_propagation_uses_exact_warehouse_sellable_authority():
    block = _function_block(
        PROPAGATION,
        "run_governed_group_propagation",
    )

    assert "requested_stock = db.session.get(" in block
    assert (
        "MarketplaceListing.master_product_group_id == group_id"
        in block
    )
    assert (
        "MarketplaceListing.warehouse_stock_id"
        in block
    )
    assert '"sellable_quantity"' in block

    stale_authorities = (
        "AmazonFBAInventory",
        "requested_quantity",
        "group_has_fba_authority",
        "target_quantity",
     )

    for stale_authority in stale_authorities:
        assert stale_authority not in block


def test_group_propagation_skips_fba_per_listing():
    propagation = _function_block(
        PROPAGATION,
        "run_governed_group_propagation",
    )
    classifier = _function_block(
        PROPAGATION,
        "_classify_listing",
    )

    assert 'if classification["skip"]:' in propagation
    assert 'if classification["is_fba"]:' in propagation
    assert '"status": (\n                    "read_only"' in propagation
    assert (
        'explicit_fba = bool(getattr(listing, "is_fba", False))'
        in classifier
    )
    assert (
        'is_fba = is_amazon and (explicit_fba or not is_fbm)'
        in classifier
    )
