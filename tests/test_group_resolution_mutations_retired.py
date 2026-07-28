def test_legacy_group_resolution_mutators_are_not_product_linking_authority():
    source = open("group_resolution.py", encoding="utf-8").read()

    # These helpers may remain for historical read compatibility, but no governed
    # Product Linking route may import or call them as a write authority.
    governed_source = open("governed_group_routes.py", encoding="utf-8").read()
    assert "add_warehouse_stock_to_group" not in governed_source
    assert "remove_warehouse_stock_from_group" not in governed_source
    assert "group_resolution" not in governed_source
