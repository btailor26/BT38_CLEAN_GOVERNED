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


def test_relationship_mutations_clear_browser_cache_before_reload():
    assert "async function clearSnapshot()" in SESSION
    assert (
        'transaction.objectStore(CACHE_STORE_NAME).delete(CACHE_KEY)'
        in SESSION
    )

    assert SESSION.count(
        "await clearSnapshot();\n      window.location.reload();"
    ) == 2


def test_daily_cache_can_no_longer_replay_pre_mutation_relationship():
    assert "state.fullLoadedAt = 0;" in SESSION
