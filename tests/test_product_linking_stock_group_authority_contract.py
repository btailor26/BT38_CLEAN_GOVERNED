import ast
from pathlib import Path


ROUTES = Path("governed_routes.py").read_text(encoding="utf-8")
PUSH = Path("services/governed_push_execution.py").read_text(encoding="utf-8")
PROPAGATION = Path("governed_group_propagation_routes.py").read_text(encoding="utf-8")
TEMPLATE = Path("templates/product_linking.html").read_text(encoding="utf-8")


def _function_block(source: str, name: str) -> str:
    marker = f"def {name}("
    start = source.index(marker)
    next_function = source.find("\ndef ", start + len(marker))
    if next_function == -1:
        return source[start:]
    return source[start:next_function]


def test_product_linking_dataset_uses_two_role_relationship_authority():
    block = _function_block(ROUTES, "governed_product_linking_data_compat")
    assert "MarketplaceListing.warehouse_stock_id.in_(" in block
    assert "current_group_id" in block
    assert "listing.master_product_group_id" in block
    assert "if current_group_id:" in block
    assert "listings_by_group.setdefault(" in block
    assert "elif listing.warehouse_stock_id:" in block
    assert "listings_by_stock.setdefault(" in block
    assert (
        'stock_group_id = (\n'
        '            getattr(stock, "master_product_group_id", None)'
        not in block
    )


def test_group_push_members_use_current_listing_relationship():
    block = _function_block(PUSH, "push_group_listings")
    assert "MarketplaceListing.master_product_group_id == group_id" in block
    assert "MarketplaceListing.warehouse_stock_id.isnot(None)" in block
    assert "WarehouseStock.master_product_group_id == group_id" not in block
    assert '"direct_group_listing_ids"' in block


def test_single_listing_automatic_group_expansion_uses_current_group_and_exact_stock():
    block = _function_block(PUSH, "push_marketplace_listing")
    assert 'getattr(listing, "master_product_group_id", None)' in block
    assert "authority_warehouse_stock_id=listing.warehouse_stock_id" in block
    assert "return push_group_listings(" in block


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
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        elif isinstance(node, ast.AugAssign):
            targets = [node.target]
        for target in targets:
            for child in ast.walk(target):
                if isinstance(child, ast.Attribute) and child.attr in relationship_fields:
                    assigned_relationship_fields.add(child.attr)
    assert assigned_relationship_fields == set()


def test_product_linking_shortcut_delegates_to_single_group_push_service():
    block = _function_block(PROPAGATION, "run_governed_group_propagation")
    assert "from services.governed_push_execution import push_group_listings" in block
    assert "return push_group_listings(" not in block  # adapter captures result for HTTP response
    assert "result = push_group_listings(" in block
    assert "authority_warehouse_stock_id=requested_warehouse_stock_id" in block
    assert "submit_governed_marketplace_action" not in block
    assert "MarketplaceListing" not in block
    assert "WarehouseStock" not in block


def test_shared_group_service_resolves_one_warehouse_quantity():
    block = _function_block(PUSH, "push_group_listings")
    assert "authority_warehouse_stock_id" in block
    assert 'target_quantity = int(getattr(authority_stock, "sellable_quantity", 0) or 0)' in block
    assert "stock.available_quantity = int(target_quantity + reserved + allocated)" in block
    assert '"target_quantity": target_quantity' in block
    assert '"one_shared_group_quantity": True' in block
    assert '"affected_listing_ids"' in block
    assert '"affected_warehouse_stock_ids"' in block


def test_product_linking_visible_push_declares_display_quantity_before_use():
    render_start = TEMPLATE.index("function renderWarehouseProducts(products)")
    render_end = TEMPLATE.index("function renderProductLinkingPagination()", render_start)
    block = TEMPLATE[render_start:render_end]
    declaration = block.index("const displayStockQuantity =")
    visible_push = block.index("const visiblePushBtn =")
    assert declaration < visible_push


def test_fba_is_skipped_before_marketplace_writer():
    listing_push = _function_block(PUSH, "push_marketplace_listing")
    group_push = _function_block(PUSH, "push_group_listings")
    assert "if _is_fba_listing(listing):" in listing_push
    assert "submit_governed_marketplace_action(" in listing_push
    assert listing_push.index("if _is_fba_listing(listing):") < listing_push.index("submit_governed_marketplace_action(")
    assert "if _is_fba_listing(listing):" in group_push
    assert '"push_status": "read_only"' in group_push
