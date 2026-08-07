from pathlib import Path
import ast


SOURCE = Path("governed_group_routes.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def function_node(name):
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == name:
                return node
    raise AssertionError(f"Function not found: {name}")


def function_source(name):
    node = function_node(name)
    return ast.get_source_segment(SOURCE, node) or ""


def test_unlink_has_missing_original_recovery_path():
    block = function_source("governed_group_unlink")

    # Unlink must have access to the permanent Warehouse relationship.
    assert "warehouse_stock" in block

    # Missing Warehouse original group must enter recovery rather
    # than releasing the listing into an ungrouped state.
    assert "master_product_group_id" in block

    assert "listing.master_product_group_id = None" not in block
    assert "listing.warehouse_stock_id = None" not in block
    assert "original_stock.master_product_group_id = None" not in block


def test_recovery_is_anchored_to_exact_warehouse_identity():
    block = function_source("governed_group_unlink")

    # Recovery identity is the persisted Warehouse relationship.
    assert "warehouse_stock_id" in block

    # Never recover identity by fuzzy marketplace/product matching.
    forbidden = (
        "ilike(",
        "contains(",
        "startswith(",
        "endswith(",
        "external_listing_id ==",
        "external_sku ==",
        "title ==",
        "asin ==",
    )

    for fragment in forbidden:
        assert fragment not in block


def test_recovery_never_clears_permanent_identity():
    block = function_source("governed_group_unlink")

    forbidden_assignments = (
        "listing.warehouse_stock_id = None",
        "original_stock.master_product_group_id = None",
    )

    for assignment in forbidden_assignments:
        assert assignment not in block


def test_unlink_never_reports_identityless_release():
    block = function_source("governed_group_unlink")

    assert '"released_to_unlinked": False' in block


def test_missing_original_must_be_recoverable_not_terminally_blocked():
    block = function_source("governed_group_unlink")

    # Once recovery is implemented, this old terminal failure must disappear.
    assert (
        "Warehouse product has no permanent original group."
        not in block
    ), (
        "Missing original group is still terminally blocked instead "
        "of entering deterministic recovery."
    )


def test_recovery_must_persist_original_before_restoring_listing():
    block = function_source("governed_group_unlink")

    # Required lifecycle:
    #
    # missing Warehouse original
    # -> resolve/create one real group
    # -> persist Warehouse original
    # -> restore listing current relationship to same group
    #
    # These field roles must both be present in the recovery implementation.
    assert "original_stock.master_product_group_id" in block
    assert "listing.master_product_group_id" in block


def test_recovery_does_not_create_identity_from_marketplace_metadata():
    block = function_source("governed_group_unlink").lower()

    forbidden_identity_sources = (
        "asin",
        "external_listing_id",
        "marketplace_item_id",
        "ebay_item_id",
        "amazon_listing_id",
    )

    for value in forbidden_identity_sources:
        assert value not in block
