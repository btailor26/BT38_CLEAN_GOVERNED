"""Retired route header compatibility shim.

The active shutdown branch uses routes.py plus shutdown_http_guard.py. This old
header file no longer imports marketplace services or exposes execution routes.
"""

from flask import Blueprint

bp = Blueprint("routes_header_disabled", __name__)

OLD_SYNC_DISABLED = True
MARKETPLACE_EXECUTION_DISABLED = True
GOVERNED_PATH_REQUIRED = True
