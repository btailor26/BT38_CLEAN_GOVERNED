from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROUTES = (ROOT / "governed_routes.py").read_text(encoding="utf-8")
CONCURRENCY_TEST = (ROOT / "test_concurrent_sales.py").read_text(encoding="utf-8")
PYTEST_GATE = (ROOT / "conftest.py").read_text(encoding="utf-8")


def test_ebay_oauth_never_selects_the_newest_store():
    oauth = ROUTES.split("def _resolve_governed_ebay_oauth_store", 1)[1]
    oauth = oauth.split("@governed_bp.post(\"/ebay-oauth/token\")", 1)[0]

    assert "order_by(Store.id.desc()).first()" not in oauth
    assert 'session["governed_ebay_oauth_store_id"] = store.id' in oauth
    assert "selected_store_id = session.get(\"governed_ebay_oauth_store_id\")" in oauth
    assert '"error": "ebay_store_selection_required"' in oauth


def test_refresh_requires_an_explicit_or_unambiguous_live_store():
    refresh = ROUTES.split("def governed_ebay_oauth_refresh_token():", 1)[1]

    assert 'payload.get("store_id") or request.args.get("store_id")' in refresh
    assert "_resolve_governed_ebay_oauth_store(selected_store_id)" in refresh
    assert "order_by(Store.id.desc()).first()" not in refresh


def test_database_writing_concurrency_tests_are_blocked_in_prod():
    assert 'app_env in {"PROD", "PRODUCTION"}' in CONCURRENCY_TEST
    assert '"ep-royal-fire-ai8c32qw" in database_uri' in CONCURRENCY_TEST
    assert 'os.getenv("BT38_ALLOW_DATABASE_TESTS")' in CONCURRENCY_TEST


def test_all_pytest_execution_is_blocked_before_collection_in_prod():
    assert "def pytest_sessionstart(session):" in PYTEST_GATE
    assert 'app_env in {"PROD", "PRODUCTION"}' in PYTEST_GATE
    assert '"ep-royal-fire-ai8c32qw"' in PYTEST_GATE
    assert 'os.getenv("BT38_ALLOW_DATABASE_TESTS")' in PYTEST_GATE
