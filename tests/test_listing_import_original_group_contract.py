from pathlib import Path
import ast


SOURCE = Path("services/governed_listing_refresh.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _function_source(name: str) -> str:
    for node in ast.walk(TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(SOURCE, node) or ""
    raise AssertionError(f"Function not found: {name}")


def test_listing_import_ensures_permanent_original_group_before_commit():
    refresh = _function_source("refresh_governed_listing_from_snapshot")

    ensure_pos = refresh.index("ensure_permanent_original_group(warehouse_stock)")
    commit_pos = refresh.index("db.session.commit()")

    assert ensure_pos < commit_pos


def test_original_group_is_owned_by_exact_warehouse_identity():
    helper = _function_source("ensure_permanent_original_group")

    assert 'getattr(stock, "master_product_group_id", None)' in helper
    assert "if existing_group_id is not None:" in helper
    assert "MasterProductGroup(" in helper
    assert "stock.master_product_group_id = int(group.id)" in helper
    assert "stock.is_group_controlled = True" in helper
    assert "stock.group_controlled_at = stock.group_controlled_at or now" in helper


def test_existing_original_group_is_preserved_without_new_group_creation():
    helper = _function_source("ensure_permanent_original_group")

    existing_guard = helper.index("if existing_group_id is not None:")
    existing_return = helper.index("return int(existing_group_id)", existing_guard)
    create_group = helper.index("group = MasterProductGroup(")

    assert existing_guard < existing_return < create_group


def test_listing_import_does_not_create_shared_product_linking_membership():
    refresh = _function_source("refresh_governed_listing_from_snapshot")

    assert "listing.master_product_group_id =" not in refresh
    assert "warehouse_stock.master_product_group_id =" not in refresh


def test_group_identity_is_not_derived_from_marketplace_metadata():
    helper = _function_source("ensure_permanent_original_group").lower()

    forbidden = (
        "external_listing_id",
        "external_sku",
        "asin",
        "marketplace_item_id",
        "amazon_listing_id",
    )

    for value in forbidden:
        assert value not in helper
