from pathlib import Path

ROUTES = Path("governed_routes.py").read_text(encoding="utf-8")
SESSION = Path("static/js/product-linking-session.js").read_text(encoding="utf-8")


def test_original_listing_survives_group_rebuild_after_unlink():
    assert "original_stock_listings = list(" in ROUTES
    assert "if current_group_listings:" in ROUTES
    assert (
        "listings_by_stock[authority_stock.id] = original_stock_listings"
        in ROUTES
    )


def test_empty_original_group_suppression_exists_only_once():
    marker = "members_temporarily_shared_elsewhere = any("
    assert ROUTES.count(marker) == 1


def test_relationship_mutations_merge_affected_records_without_full_reload():
    assert "async function clearSnapshot()" in SESSION
    assert (
        'transaction.objectStore(CACHE_STORE_NAME).delete(CACHE_KEY)'
        in SESSION
    )

    link_start = SESSION.index("window.linkListingToWarehouse = async function")
    unlink_start = SESSION.index("window.unlinkListing = function", link_start)
    confirm_start = SESSION.index("async function confirmExplicitUnlink()", unlink_start)
    wire_start = SESSION.index("function wire()", confirm_start)

    link_block = SESSION[link_start:unlink_start]
    unlink_block = SESSION[confirm_start:wire_start]

    assert "await applyMutationContract(data, {" in link_block
    assert "await applyMutationContract(data, {" in unlink_block
    assert "await clearSnapshot();" not in link_block
    assert "await clearSnapshot();" not in unlink_block
    assert "window.location.reload();" not in link_block
    assert "window.location.reload();" not in unlink_block


def test_persistent_session_has_no_daily_expiry_rule():
    assert "CACHE_TTL_MS" not in SESSION
    assert "fetchFullSnapshotOnceDaily" not in SESSION
    assert "fetchInitialSnapshotOnce" in SESSION
