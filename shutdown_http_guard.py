"""BT38 shutdown HTTP guard.

Fail-closes retired marketplace, debug, setup, and legacy sync HTTP
surfaces before route handlers can run while the governed path is rebuilt.
"""

from __future__ import annotations

import logging
from typing import Iterable

HTTP_GUARD_INSTALLED = False
SHUTDOWN_HTTP_GUARD_ENABLED = True

BLOCKED_EXACT_PATHS = {
    "/api/push-sku",
    "/api/classify-listings",
    "/push_stock",
    "/api/diagnostics/ebay/health",
    "/api/diagnostics/ebay/policies",
    "/api/diagnostics/ebay/raw-import",
    "/api/diagnostics/amazon/auth",
    "/api/admin/fix-sandbox-flag",
    "/api/admin/ebay/normalize-itemids",
    "/ebay-setup",
    "/test-ebay-connection",
    "/test/ebay-push",
    "/debug/fba-local",
    "/debug/fba-local-direct",
    "/debug/fba-open",
}

BLOCKED_PREFIXES = (
    "/sync/run/",
    "/api/test/ebay-push/",
    "/api/sync/amazon/sku/",
    "/api/sync/ebay/sku/",
    "/stores/sync/",
    "/groups/",
    "/push_stock/",
    "/api/push/",
)


def is_shutdown_path(path: str, exact_paths: Iterable[str] = BLOCKED_EXACT_PATHS) -> bool:
    """Return True when a request path belongs to a retired marketplace surface."""
    if path in exact_paths:
        return True
    return any(path.startswith(prefix) for prefix in BLOCKED_PREFIXES)


def install_shutdown_http_guard() -> bool:
    """Install a global Flask preprocess_request guard exactly once."""
    global HTTP_GUARD_INSTALLED

    if HTTP_GUARD_INSTALLED:
        return True

    try:
        from flask import Flask, jsonify, request
    except Exception as exc:  # pragma: no cover - dependency install failure
        logging.error("[SHUTDOWN_HTTP_GUARD] Flask/request unavailable; guard not installed: %s", exc)
        return False

    original_preprocess_request = Flask.preprocess_request

    def guarded_preprocess_request(self):
        if SHUTDOWN_HTTP_GUARD_ENABLED and is_shutdown_path(request.path):
            logging.warning(
                "[SHUTDOWN_HTTP_GUARD] Blocked retired marketplace path: %s",
                request.path,
            )
            return (
                jsonify(
                    {
                        "success": False,
                        "ok": False,
                        "execution_blocked": True,
                        "route_retired": True,
                        "shutdown_http_guard": True,
                        "path": request.path,
                        "error": "This old marketplace/sync route is disabled during governed-path rebuild.",
                    }
                ),
                410,
            )
        return original_preprocess_request(self)

    Flask.preprocess_request = guarded_preprocess_request
    HTTP_GUARD_INSTALLED = True
    logging.warning("[SHUTDOWN_HTTP_GUARD] Installed global retired marketplace route blocker.")
    return True


install_shutdown_http_guard()
