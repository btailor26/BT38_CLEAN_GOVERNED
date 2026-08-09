from pathlib import Path


SESSION = Path("static/js/product-linking-session.js").read_text(
    encoding="utf-8"
)
REFRESH = Path("services/governed_listing_refresh.py").read_text(
    encoding="utf-8"
)
GROUPS = Path("governed_group_routes.py").read_text(
    encoding="utf-8"
)


def test_imported_listing_gets_same_original_group_as_warehouse_identity():
    assert "original_group_id = ensure_permanent_original_group(warehouse_stock)" in REFRESH
    assert "master_product_group_id=int(original_group_id)" in REFRESH
    assert 'if getattr(listing, "master_product_group_id", None) is None:' in REFRESH
    assert "listing.master_product_group_id = int(original_group_id)" in REFRESH


def test_unlink_returns_listing_to_original_group_not_null():
    assert "resulting_group_id = int(original_group_id)" in GROUPS
    assert "listing.master_product_group_id = resulting_group_id" in GROUPS
    assert "listing.master_product_group_id = None" not in GROUPS
    assert '"restored_original_group": restored_original_group' in GROUPS
    assert '"released_from_shared_group": True' in GROUPS


def test_missing_group_id_is_visible_and_group_push_is_blocked():
    assert 'Missing Group ID' in SESSION
    assert 'Group push blocked: permanent Group ID is missing' in SESSION
    assert "groupPushButton.disabled = true" in SESSION
    assert 'Relationship BLOCKED · no Group ID' in SESSION


def test_product_linking_reads_existing_settings_authority_only():
    assert 'fetch("/governed/settings/state"' in SESSION
    assert '"push_enabled"' in SESSION
    assert '"runtime_push_enabled"' in SESSION
    assert '"marketplace_push_enabled"' in SESSION
    assert '"manual_push_enabled"' in SESSION
    assert "config.quantity_push_enabled" in SESSION
    assert "config.group_push_enabled" in SESSION
    assert "store?.auto_push_enabled" in SESSION


def test_push_settings_are_rendered_as_evidence_not_new_controls():
    assert "bt38-push-settings-evidence" in SESSION
    assert '`Global ${globalOn ? "ON" : "OFF"}`' in SESSION
    assert '`Qty ${quantityOn ? "ON" : "OFF"}`' in SESSION
    assert '`Group ${groupOn ? "ON" : "OFF"}`' in SESSION
    assert '`Auto ${autoOn ? "ON" : "OFF"}`' in SESSION
    assert "/governed/settings/config" not in SESSION
    assert "/governed/settings/stores/" not in SESSION
