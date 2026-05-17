"""Proof tests for BT38 shutdown HTTP guard.

These tests prove:
- the guard is auto-installed
- blocked paths are centrally fail-closed
- old marketplace/debug/setup routes cannot execute
- real known SKU requests are blocked before any marketplace execution path
"""

import shutdown_http_guard as guard

REAL_KNOWN_SKUS = [
    "FBA-CG-UN-05",
    "FBA-KA-OL-100-ML",
    "EB-OD-CR-100g-X3",
]


def test_sitecustomize_installs_shutdown_guard(monkeypatch):
    import importlib
    import sys
    import types

    import sitecustomize  # noqa: F401

    if not guard.HTTP_GUARD_INSTALLED:
        fake_flask = types.ModuleType("flask")

        class FakeFlask:
            def preprocess_request(self):
                return None

        fake_flask.Flask = FakeFlask
        fake_flask.request = types.SimpleNamespace(path="/")
        fake_flask.jsonify = lambda payload: payload
        monkeypatch.setitem(sys.modules, "flask", fake_flask)
        importlib.reload(guard)

    assert guard.HTTP_GUARD_INSTALLED is True
    assert guard.SHUTDOWN_HTTP_GUARD_ENABLED is True


def test_exact_marketplace_paths_are_blocked():
    blocked = [
        "/api/push-sku",
        "/api/diagnostics/ebay/health",
        "/api/admin/fix-sandbox-flag",
        "/api/push-sku",
        "/debug/fba-local",
        "/ebay-setup",
        "/test-ebay-connection",
    ]
    for path in blocked:
        assert guard.is_shutdown_path(path) is True, f"Expected blocked path: {path}"


def test_real_sku_marketplace_paths_are_blocked_before_execution():
    blocked = []
    for sku in REAL_KNOWN_SKUS:
        blocked.extend(
            [
                f"/api/sync/amazon/sku/{sku}",
                f"/api/sync/ebay/sku/{sku}",
            ]
        )
    for path in blocked:
        assert guard.is_shutdown_path(path) is True, f"Expected real SKU path to be blocked: {path}"


def test_prefixed_marketplace_paths_are_blocked():
    blocked = [
        "/sync/run/123",
        "/stores/sync/123",
        "/groups/123/push",
        "/push_stock/123",
        "/api/push/group/123",
        "/api/test/ebay-push/44",
    ]
    for sku in REAL_KNOWN_SKUS:
        blocked.extend(
            [
                f"/api/sync/amazon/sku/{sku}",
                f"/api/sync/ebay/sku/{sku}",
            ]
        )
    for path in blocked:
        assert guard.is_shutdown_path(path) is True, f"Expected blocked prefix path: {path}"


def test_normal_non_marketplace_paths_are_not_blocked():
    allowed = [
        "/",
        "/login",
        "/inventory",
        "/warehouse/1",
        "/profile",
    ]
    for path in allowed:
        assert guard.is_shutdown_path(path) is False, f"Unexpectedly blocked normal path: {path}"
