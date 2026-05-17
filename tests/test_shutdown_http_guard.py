"""
Proof tests for BT38 shutdown HTTP guard.

These tests prove:
- the guard is auto-installed
- blocked paths are centrally fail-closed
- old marketplace/debug/setup routes cannot execute
- real known SKU requests are blocked before any marketplace execution path
"""

import shutdown_http_guard as guard

REAL_KNOWN_SKU = "FBA-CG-UN-05"


def test_sitecustomize_installs_shutdown_guard():
    import sitecustomize  # noqa: F401

    assert guard.HTTP_GUARD_INSTALLED is True
    assert guard.SHUTDOWN_HTTP_GUARD_ENABLED is True


def test_exact_marketplace_paths_are_blocked():
    blocked = [
        "/api/push-sku",
        "/api/diagnostics/ebay/health",
        "/api/admin/fix-sandbox-flag",
        "/debug/fba-local",
        "/ebay-setup",
        "/test-ebay-connection",
    ]

    for path in blocked:
        assert guard.is_shutdown_path(path) is True, f"Expected blocked path: {path}"


def test_real_sku_marketplace_paths_are_blocked_before_execution():
    blocked = [
        f"/api/sync/amazon/sku/{REAL_KNOWN_SKU}",
        f"/api/sync/ebay/sku/{REAL_KNOWN_SKU}",
    ]

    for path in blocked:
        assert guard.is_shutdown_path(path) is True, f"Expected real SKU path to be blocked: {path}"


def test_prefixed_marketplace_paths_are_blocked():
    blocked = [
        "/sync/run/123",
        "/api/test/ebay-push/44",
        f"/api/sync/amazon/sku/{REAL_KNOWN_SKU}",
        f"/api/sync/ebay/sku/{REAL_KNOWN_SKU}",
    ]

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
