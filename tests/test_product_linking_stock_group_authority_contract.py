from pathlib import Path


ROUTES = Path("governed_routes.py").read_text(encoding="utf-8")
PUSH = Path(
    "services/governed_push_execution.py"
).read_text(encoding="utf-8")


def _function_block(source: str, name: str) -> str:
    marker = f"def {name}("
    start = source.index(marker)
    next_function = source.find("\ndef ", start + len(marker))
    if next_function == -1:
        return source[start:]
    return source[start:next_function]


def test_product_linking_dataset_uses_warehouse_stock_group_authority():
    block = _function_block(
        ROUTES,
        "governed_product_linking_data_compat",
    )

    assert (
        "MarketplaceListing.warehouse_stock_id.in_("
        in block
    )
    assert (
        'stock_group_id = (\n'
        '            getattr(stock, "master_product_group_id", None)'
        in block
    )

    forbidden = (
        "MarketplaceListing.master_product_group_id.in_(",
        'current_group_id = getattr(\n'
        '            listing,\n'
        '            "master_product_group_id"',
    )

    for value in forbidden:
        assert value not in block


def test_group_push_members_are_resolved_only_through_warehouse_stock():
    block = _function_block(PUSH, "push_group_listings")

    assert (
        "WarehouseStock.master_product_group_id == group_id"
        in block
    )
    assert (
        "MarketplaceListing.warehouse_stock_id.in_("
        in block
    )
    assert (
        "MarketplaceListing.master_product_group_id == group_id"
        not in block
    )
    assert "direct_group_listing_ids" not in block


def test_single_listing_automatic_group_expansion_has_no_listing_fallback():
    block = _function_block(PUSH, "push_marketplace_listing")

    assert (
        'getattr(\n'
        '        listing.warehouse_stock,\n'
        '        "master_product_group_id"'
        in block
    )
    assert (
        'or getattr(listing, "master_product_group_id", None)'
        not in block
    )


def test_push_does_not_assign_relationship_fields():
    block = _function_block(PUSH, "push_group_listings")

    forbidden_assignments = (
        ".master_product_group_id =",
        ".warehouse_stock_id =",
        ".is_group_controlled =",
    )

    for assignment in forbidden_assignments:
        assert assignment not in block
