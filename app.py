from flask_login import login_required, current_user
from flask_login import login_required
import os
import logging
import time
import subprocess
import sys
try:
    import fcntl
except ImportError:
    fcntl = None
from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix
from extensions import db, login_manager

# NEVER-AGAIN PROTECTION: Fail fast if critical modules have syntax errors
# This prevents unclosed triple-quote blocks from crashing the app at runtime
CRITICAL_MODULES = [
    'routes.py',
    'sync_service.py',
    'amazon_service.py',
    'ebay_service.py',
    'governed_fbm_routes.py',
    'fbm_models.py',
    'services/fbm_provider_contract.py',
    'services/fbm_order_mapper.py',
    'services/fbm_amazon_order_profile.py',
    'services/fbm_amazon_shipping_adapter.py',
    'services/fbm_packlink_adapter.py',
    'services/fbm_carrier_mapping.py',
    'services/fbm_post_purchase.py',
    'services/fbm_shipping_state.py',
]

def validate_syntax_on_startup():
    """Compile-check critical modules before Flask loads them."""
    for module in CRITICAL_MODULES:
        module_path = os.path.join(os.path.dirname(__file__), module)
        if os.path.exists(module_path):
            result = subprocess.run(
                [sys.executable, '-m', 'py_compile', module_path],
                capture_output=True, text=True
            )
            if result.returncode != 0:
                error_msg = f"FATAL: Syntax error in {module}:\n{result.stderr}"
                logging.error(error_msg)
                raise SyntaxError(error_msg)
    logging.info("Startup syntax check passed for all critical modules")

validate_syntax_on_startup()

# App version for cache busting and deployment verification
# Update this with each deployment to ensure templates are refreshed
APP_VERSION = f"1.0.{int(time.time())}"  # Dynamic versioning based on startup time

# Environment configuration - DEV MODE ONLY (production deployment disabled)
APP_ENV = os.getenv("APP_ENV", "dev").lower()  # Default to dev for localhost-only development
IS_PRODUCTION = APP_ENV == "prod"
IS_DEVELOPMENT = APP_ENV == "dev"
IS_STAGING = APP_ENV == "staging"

# Staging safety: PUSH_ENABLED controls whether write operations are allowed
# Default: True in prod/dev, False in staging
PUSH_ENABLED = os.getenv("PUSH_ENABLED", "true" if not IS_STAGING else "false").lower() == "true"
EXECUTION_MODE = os.getenv("EXECUTION_MODE", "read-write" if not IS_STAGING else "read-only").lower()

# ============================================================================
# SENTINEL-2: UNLOCK MODES
# ============================================================================
# SENTINEL_MODE controls what Sentinel can do:
#   LOCKED  - No input, no execution (default)
#   OBSERVE - Read-only observation (current staging behavior)
#   PLAN    - Accept command submissions for validation (NO execution)
#   EXECUTE - NOT ENABLED (blocked at code level)
SENTINEL_MODE = os.getenv("SENTINEL_MODE", "OBSERVE").upper()
VALID_SENTINEL_MODES = ["LOCKED", "OBSERVE", "PLAN"]
if SENTINEL_MODE not in VALID_SENTINEL_MODES:
    logging.warning(f"Invalid SENTINEL_MODE '{SENTINEL_MODE}', defaulting to OBSERVE")
    SENTINEL_MODE = "OBSERVE"
# HARD BLOCK: EXECUTE mode is never allowed
if SENTINEL_MODE == "EXECUTE":
    logging.error("SENTINEL_MODE=EXECUTE is BLOCKED - forcing LOCKED")
    SENTINEL_MODE = "OBSERVE"

# [STAGE5] Single-Writer Failover Guards
# These flags control whether this instance can perform commercial actions
FAILOVER_ROLE = os.getenv("FAILOVER_ROLE", "primary").lower()
IS_ACTIVE_PRIMARY = os.getenv("IS_ACTIVE_PRIMARY", "true").lower() == "true"
ENABLE_SYNC_WORKERS = os.getenv("ENABLE_SYNC_WORKERS", "true").lower() == "true"
ENABLE_PUSH_JOBS = os.getenv("ENABLE_PUSH_JOBS", "true").lower() == "true"
ENABLE_SCHEDULERS = os.getenv("ENABLE_SCHEDULERS", "true").lower() == "true"

# Configure logging
log_level = logging.DEBUG if IS_DEVELOPMENT else logging.INFO
logging.basicConfig(level=log_level)

# Create the app
app = Flask(__name__)
app.config["BT38_ASSET_VERSION"] = APP_VERSION

# Database configuration
import os
# Production and development both use PostgreSQL/Neon.
# SQLite is not supported.


# SECURITY: Require SESSION_SECRET in production - fail fast if missing
session_secret = os.environ.get("SESSION_SECRET")
if not session_secret:
    if IS_PRODUCTION:
        raise RuntimeError("CRITICAL: SESSION_SECRET environment variable must be set in production")
    else:
        logging.warning("SESSION_SECRET not set - using dev fallback (NOT SAFE FOR PRODUCTION)")
        session_secret = "dev-secret-key-change-in-production"

app.secret_key = session_secret
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Store environment in app config for access throughout the app
app.config["APP_ENV"] = APP_ENV
app.config["IS_PRODUCTION"] = IS_PRODUCTION
app.config["IS_DEVELOPMENT"] = IS_DEVELOPMENT
app.config["IS_STAGING"] = IS_STAGING
app.config["PUSH_ENABLED"] = PUSH_ENABLED
app.config["EXECUTION_MODE"] = EXECUTION_MODE
app.config["SENTINEL_MODE"] = SENTINEL_MODE

# Session configuration - use standard Flask sessions for compatibility
app.config['SESSION_PERMANENT'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = 1800  # 30 minutes

# ============================================================================
# SESSION COOKIE ISOLATION - CRITICAL FOR STAGING/PRODUCTION SEPARATION
# ============================================================================
# Each environment gets a UNIQUE cookie name to prevent session leakage
# Cookie DOMAIN is set dynamically based on request host (see after_request)

# Environment-scoped cookie name (prevents cross-environment session sharing)
app.config['SESSION_COOKIE_NAME'] = f"bt38_session_{APP_ENV}"

# Cookie domain: Set to None by default - will be set dynamically per-request
# This allows the app to work on BOTH:
# - Custom domains (bt38inv.com, staging.bt38inv.com)
# - Replit preview domains (*.replit.dev)
app.config['SESSION_COOKIE_DOMAIN'] = None  # Dynamic - see after_request handler

# Store the current environment in session for mismatch detection
app.config['BT38_SESSION_ENV'] = APP_ENV

# Session cookie settings
app.config['SESSION_COOKIE_SECURE'] = True  # Required for SameSite=None
app.config['SESSION_COOKIE_SAMESITE'] = 'None'  # Required for cross-site iframe (Replit wrapper)
app.config['SESSION_COOKIE_HTTPONLY'] = True

# Template configuration - force reload to ensure new templates are picked up after deployment
app.config['TEMPLATES_AUTO_RELOAD'] = True  # Always reload templates on change
app.jinja_env.auto_reload = True  # Force Jinja2 to check template modification times

# DEV and PROD both use PostgreSQL/Neon for database parity.
if IS_DEVELOPMENT:
    dev_db_url = (
        os.environ.get("DEV_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
    )

    if not dev_db_url:
        raise RuntimeError(
            "DEV cannot start: set DEV_DATABASE_URL or DATABASE_URL "
            "to the Neon PostgreSQL connection string."
        )

    if not dev_db_url.startswith(
        ("postgresql://", "postgres://")
    ):
        raise RuntimeError(
            "DEV database must be PostgreSQL/Neon. "
            "SQLite and other database engines are not supported."
        )

    app.config["SQLALCHEMY_DATABASE_URI"] = dev_db_url
    logging.info("DEV MODE: Using PostgreSQL/Neon database")
else:
    prod_db_url = os.environ.get("DATABASE_URL")

    if not prod_db_url:
        raise RuntimeError(
            "CRITICAL: DATABASE_URL must be set in production"
        )

    if not prod_db_url.startswith(
        ("postgresql://", "postgres://")
    ):
        raise RuntimeError(
            "Production database must be PostgreSQL/Neon."
        )

    app.config["SQLALCHEMY_DATABASE_URI"] = prod_db_url
    logging.info("PROD MODE: Using PostgreSQL/Neon DATABASE_URL")

app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_recycle": 300,
    "pool_pre_ping": True,
    "echo": IS_DEVELOPMENT,  # Only log SQL queries in development
}
app.config["SQLALCHEMY_ECHO"] = IS_DEVELOPMENT  # Match engine setting

# Initialize the extensions with the app
db.init_app(app)
login_manager.init_app(app)
# Enable login requirement - all routes require authentication
login_manager.login_view = 'governed.login'  # Governed blueprint route
login_manager.login_message = 'Please log in to access this page.'
login_manager.login_message_category = 'warning'

# Custom unauthorized handler to use RELATIVE paths (not absolute URLs)
@login_manager.unauthorized_handler
def unauthorized():
    from flask import flash, jsonify, redirect, url_for, request
    import logging

    logging.info(f"[UNAUTH] Custom handler called for path: {request.path}")

    governed_api_prefixes = (
        "/governed/actions/",
        "/governed/product-linking/",
        "/governed/groups/",
    )

    if request.path.startswith(governed_api_prefixes):
        return jsonify({
            "success": False,
            "ok": False,
            "governed": True,
            "reason": "authentication_required",
            "message": "Authentication is required.",
        }), 401

    flash(
        login_manager.login_message,
        login_manager.login_message_category,
    )

    # Browser/page requests continue to use the governed login page.
    return redirect(
        url_for("governed.login", next=request.path)
    )

# Add custom Jinja2 filter to parse JSON strings
@app.template_filter('from_json')
def from_json_filter(value):
    """Parse JSON string to Python object"""
    import json
    try:
        return json.loads(value) if value else {}
    except (ValueError, TypeError):
        return {}

@login_manager.user_loader
def load_user(user_id):
    from models import User
    return User.query.get(int(user_id))

@app.after_request
def add_cache_control(response):
    """Prevent browser caching for HTML pages to ensure fresh data"""
    if response.content_type and 'text/html' in response.content_type:
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response

def migrate_database():
    """Apply database migrations and ensure schema is up to date.

    Uses safe column addition that handles existing columns gracefully.
    Each ALTER is executed in its own transaction to prevent batch failures.
    """
    try:
        # Import models to ensure tables are created. FBM shipment models are
        # isolated from MarketplaceOrder but still registered on the same DB
        # metadata before the existing safe create_all() call.
        import models
        import fbm_models

        # Create all tables (this is safe - won't drop existing data)
        db.create_all()
        db.session.commit()

        logging.info("Database tables created/verified successfully")

    except Exception as e:
        logging.error(f"Database migration failed: {str(e)}")
        try:
            db.session.rollback()
        except:
            pass
        # Continue anyway - tables might already exist

# Helper to check if request wants JSON
def _wants_json():
    """Check if the request is for an API route that expects JSON"""
    from flask import request
    return request.path.startswith('/api/')

# Helper to accept both JSON and form data
def get_json_or_form():
    """
    Try JSON first (even if Content-Type isn't perfect), then form, then files.
    Prevents 415 Unsupported Media Type errors.
    """
    from flask import request
    data = request.get_json(silent=True)
    if data is not None:
        return data
    if request.form:
        return request.form.to_dict(flat=True)
    if request.files:
        fields = request.values.to_dict(flat=True)
        fields['_files'] = list(request.files.keys())
        return fields
    return {}

# Prevent login redirects on API routes (return 401 JSON instead)
@app.before_request
def api_auth_json():
    """
    For API routes, return 401 JSON instead of redirecting to login
    This prevents HTML responses that break JSON parsing
    """
    from flask import request, jsonify
    from flask_login import current_user, login_required

    if request.path.startswith('/api/') and not current_user.is_authenticated:
        public_endpoints = [
            '/api/sync-status',
            '/api/diagnostics/system',
            '/api/diagnostics/ebay/health',
            '/api/diagnostics/amazon/health',
            '/api/system/health',
            '/api/system/env-check',
            '/api/system/log_route',
            '/api/system/log_route_failure',
            '/api/system/fingerprint',
            '/api/sentinel/status'
        ]
        mobile_prefixes = ['/api/mobile/', '/api/carton']
        for prefix in mobile_prefixes:
            if request.path.startswith(prefix):
                return None
        task_api_key = os.environ.get("TASK_API_KEY")
        if task_api_key and request.headers.get("X-Task-Key") == task_api_key:
            return None
        if request.path not in public_endpoints:
            return jsonify(ok=False, error="unauthorized"), 401


# BT38 GOVERNED ROUTE LOCK
# Legacy operational write routes are disabled.
# Governed routes remain active.
LEGACY_OPERATIONAL_ROUTES_ENABLED = False
GOVERNED_OPERATIONAL_ROUTES_ENABLED = True

@app.before_request
def bt38_block_legacy_operational_write_routes():
    from flask import request, jsonify

    if LEGACY_OPERATIONAL_ROUTES_ENABLED:
        return None

    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None

    path = request.path.rstrip("/") or "/"

    # Governed execution layer remains the only supported operational write path.
    # FBM write endpoints are explicit, authenticated, confirmation-gated routes
    # owned by governed_fbm_routes; they do not reopen any retired legacy route.
    if path.startswith("/governed/"):
        return None

    legacy_exact = {
        "/inventory/delete_bulk",
        "/mock-disabled-action",
        "/search_ungrouped_items",
        "/api/group-push",
        "/api/listings/bulk-action",
        "/api/test-connection",
        "/api/test-amazon-connection",
        "/api/test-ebay-connection",
        "/test-ebay-connection",
        "/push_stock_bulk",
        "/push_stock_all",
        "/admin/api/backfill",
    }

    legacy_prefixes = (
        "/api/group-push/",
        "/api/listings/bulk",
        "/api/product-linking",
        "/api/product_linking",
        "/api/link_listing_to_warehouse",
        "/api/unlink_listing",
        "/api/bulk_link_products",
        "/api/warehouse/",
        "/api/stock_transfer/",
        "/api/shelf/",
        "/inventory/delete_bulk",
        "/mock-disabled-action",
        "/search_ungrouped_items",
        "/groups/",
        "/release_from_group/",
        "/add_sku_to_group/",
        "/push_stock/",
        "/warehouse/upload-image/",
        "/auth/amazon",
        "/bt38-setup",
        "/ebay-setup",
    )

    legacy_listing_push = path.startswith("/api/listings/") and path.endswith("/push")

    if path in legacy_exact or path.startswith(legacy_prefixes) or legacy_listing_push:
        return jsonify({
            "success": False,
            "ok": False,
            "legacy_route_disabled": True,
            "governed_required": True,
            "message": "This legacy operational route is disabled. Use the governed route."
        }), 409

    return None

# HTTP Exception handler (catches 400, 403, 404, 405, etc.)
@app.errorhandler(Exception)
def handle_http_exception(e):
    """
    Handle HTTP exceptions (404, 405, etc.) with JSON for API routes
    """
    from flask import jsonify, render_template
    from werkzeug.exceptions import HTTPException
    import traceback
    logging.error(f"Exception occurred: {e.__class__.__name__}: {str(e)}")
    if isinstance(e, HTTPException):
        if _wants_json():
            return jsonify(ok=False, error=e.description or str(e)), e.code
        return e
    logging.error("Full traceback:")
    logging.error(traceback.format_exc())
    if _wants_json():
        return jsonify(ok=False, error=str(e)), 500
    from datetime import datetime
    return render_template('error.html', error_code=500, error_title="Unexpected Error", error_message="An unexpected error occurred. Please try again.", now=datetime.utcnow()), 500

# Specific handlers for common HTTP errors
@app.errorhandler(404)
def handle_404(e):
    from flask import jsonify, render_template
    if _wants_json():
        return jsonify(ok=False, error='Not found'), 404
    from datetime import datetime
    return render_template('error.html', error_code=404, error_title="Page Not Found", error_message="The page you're looking for doesn't exist.", now=datetime.utcnow()), 404

# NOTE: remainder of app.py retained from existing governed branch below this point.
