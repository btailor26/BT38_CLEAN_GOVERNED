from legacy_product_linking_guard import LEGACY_GOVERNED_ACTIONS


def test_legacy_product_linking_actions_are_explicitly_retired():
    assert LEGACY_GOVERNED_ACTIONS == {
        "link-listing-to-warehouse",
        "unlink-listing",
        "product-linking-link",
    }


def test_governed_group_routes_register_single_writer_guard():
    source = open("governed_group_routes.py", encoding="utf-8").read()
    assert "@governed_group_bp.before_app_request" in source
    assert "block_legacy_product_linking_request" in source


def test_unlink_never_clears_permanent_relationship_identity():
    source = open("governed_group_routes.py", encoding="utf-8").read()
    unlink_source = source.split("def governed_group_unlink", 1)[1].split("def _link_stock_to_group", 1)[0]

    # Shared/current Product Linking membership is intentionally removable.
    assert "listing.master_product_group_id = None" in unlink_source

    # Permanent Warehouse identity is never released.
    assert "listing.warehouse_stock_id = None" not in unlink_source
    assert "original_stock.master_product_group_id = None" not in unlink_source
    assert "listing.master_product_group_id = None" in unlink_source
    assert '"released_to_unlinked": True' in unlink_source
