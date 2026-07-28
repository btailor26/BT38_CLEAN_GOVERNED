def test_governed_mutation_contract_never_requests_full_dataset_refresh():
    source = open("governed_group_routes.py", encoding="utf-8").read()
    assert '"full_dataset_refresh": False' in source
    assert '"refresh_scope": "affected_rows" if changed else "none"' in source


def test_legacy_guard_fails_closed_without_database_access():
    source = open("legacy_product_linking_guard.py", encoding="utf-8").read()
    assert "from extensions import db" not in source
    assert "db.session" not in source
    assert "legacy_product_linking_disabled" in source
