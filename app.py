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
CRITICAL_MODULES = ['routes.py', 'sync_service.py', 'amazon_service.py', 'ebay_service.py']

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

# === BT38 FORCE SQLITE (DEV ONLY) ===
import os
# DATABASE_URL must never be removed here.
# Production uses DATABASE_URL. Local dev may use DEV_DATABASE_URL or explicit SQLite dev fallback.


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

# Configure the database - DEV and PROD both use PostgreSQL for parity
if IS_DEVELOPMENT:
    # DEV MODE: Use DEV_DATABASE_URL or DATABASE_URL (both must be PostgreSQL)
    # SQLite is BLOCKED by default to ensure DEV/PROD parity
    dev_db_url = os.environ.get("DEV_DATABASE_URL") or os.environ.get("DATABASE_URL") or "sqlite:///C:/Users/btail/_ARCHIVE_OLD_BT38/BT38/instance/bt38_ims_local.db"

    if not dev_db_url:
        raise RuntimeError(
            "DEV cannot start: No database URL configured. "
            "Set DEV_DATABASE_URL or DATABASE_URL to a PostgreSQL connection string."
        )

    # Block SQLite unless explicitly allowed (for emergency local testing only)
    if dev_db_url.startswith("sqlite"):
        allow_sqlite = os.environ.get("ALLOW_SQLITE_DEV", "false").lower() == "true"
        if not allow_sqlite:
            raise RuntimeError(
                "DEV cannot use SQLite. DEV must match PROD (PostgreSQL). "
                "Set DEV_DATABASE_URL to Postgres, or set ALLOW_SQLITE_DEV=true (temporary only)."
            )
        logging.critical("⚠️  SQLITE DEV OVERRIDE ENABLED — GOVERNANCE BYPASS ACTIVE")

    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///C:/Users/btail/_ARCHIVE_OLD_BT38/BT38/instance/bt38_ims_local.db"
    logging.info(f"DEV MODE: Using database: {dev_db_url.split('@')[-1] if '@' in dev_db_url else 'configured'}")
else:
    # PROD MODE: Use DATABASE_URL (should be set by Replit deployment)
    prod_db_url = os.environ.get("DATABASE_URL")
    if not prod_db_url:
        raise RuntimeError("CRITICAL: DATABASE_URL must be set in production environment")
    app.config["SQLALCHEMY_DATABASE_URI"] = prod_db_url
    logging.info("PROD MODE: Using DATABASE_URL")

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
    from flask import flash, redirect, url_for, request
    import logging
    logging.info(f"[UNAUTH] Custom handler called for path: {request.path}")
    flash(login_manager.login_message, login_manager.login_message_category)
    # Use request.path (relative) instead of request.url (absolute with host)
    return redirect(url_for('governed.login', next=request.path))

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
        # Import models to ensure tables are created
        import models

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

    # Try JSON first (silent=True doesn't raise on parse errors)
    data = request.get_json(silent=True)
    if data is not None:
        return data

    # Fall back to form data
    if request.form:
        return request.form.to_dict(flat=True)

    # Fall back to files (with field metadata)
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
        # Check if this is a public API endpoint or has task API key
        public_endpoints = [
            '/api/sync-status',
            '/api/diagnostics/system',
            '/api/diagnostics/ebay/health',
            '/api/diagnostics/amazon/health',
            '/api/system/health',  # Section X.5: Fast health check
            '/api/system/env-check',  # Section 8: Environment check
            '/api/system/log_route',  # Section X.9: Route logging
            '/api/system/log_route_failure',  # Section X.9: Route failure logging
            '/api/system/fingerprint',  # Sentinel: Environment fingerprint (no auth)
            '/api/sentinel/status'  # Sentinel-2: Status API (read-only, no auth)
        ]  # Add public endpoints here - SendGrid test requires authentication

        # Section 3 & 4: Mobile Scanning and Carton API endpoints (prefix match for dynamic routes)
        mobile_prefixes = ['/api/mobile/', '/api/carton']
        for prefix in mobile_prefixes:
            if request.path.startswith(prefix):
                return None  # Allow mobile/carton API requests

        # Allow endpoints with valid task API key
        task_api_key = os.environ.get("TASK_API_KEY")
        if task_api_key and request.headers.get("X-Task-Key") == task_api_key:
            return None  # Allow the request to proceed

        if request.path not in public_endpoints:
            return jsonify(ok=False, error="unauthorized"), 401

# HTTP Exception handler (catches 400, 403, 404, 405, etc.)
@app.errorhandler(Exception)
def handle_http_exception(e):
    """
    Handle HTTP exceptions (404, 405, etc.) with JSON for API routes
    """
    from flask import jsonify, render_template
    from werkzeug.exceptions import HTTPException
    import traceback

    # Log the error with full traceback
    logging.error(f"Exception occurred: {e.__class__.__name__}: {str(e)}")

    if isinstance(e, HTTPException):
        # For API routes, return JSON
        if _wants_json():
            return jsonify(ok=False, error=e.description or str(e)), e.code
        # For non-API routes, use default HTML error page
        return e

    # For non-HTTP exceptions, log full traceback
    logging.error("Full traceback:")
    logging.error(traceback.format_exc())

    # For API routes, always return JSON
    if _wants_json():
        return jsonify(ok=False, error=str(e)), 500

    # For non-API routes, render error template
    from datetime import datetime
    return render_template('error.html', 
                         error_code=500,
                         error_title="Unexpected Error",
                         error_message="An unexpected error occurred. Please try again.",
                         now=datetime.utcnow()), 500

# Specific handlers for common HTTP errors
@app.errorhandler(404)
def handle_404(e):
    """Handle 404 Not Found with JSON for API routes"""
    from flask import jsonify
    if _wants_json():
        return jsonify(ok=False, error="not found"), 404
    return e

@app.errorhandler(405)
def handle_405(e):
    """Handle 405 Method Not Allowed with JSON for API routes"""
    from flask import jsonify
    if _wants_json():
        return jsonify(ok=False, error="method not allowed"), 405
    return e

def ensure_production_ebay_sandbox_flag():
    """
    CRITICAL PRODUCTION FIX: Ensure all eBay stores have explicit sandbox=false flag
    This fixes production imports returning 0 items due to defaulting to sandbox API
    Runs only in production, idempotent, swallows errors to avoid boot failures
    """
    if not IS_PRODUCTION:
        return  # Only run in production

    try:
        import json as json_module
        from models import Store

        ebay_stores = Store.query.filter_by(platform='eBay', is_active=True).all()

        for store in ebay_stores:
            if not store.api_key:
                continue

            try:
                creds = json_module.loads(store.api_key)
                sandbox_value = creds.get('sandbox')

                # Fix missing or True sandbox flag
                if sandbox_value is None or sandbox_value is True:
                    logging.warning(f"PRODUCTION FIX: Store '{store.name}' missing sandbox:false flag (was: {sandbox_value}), setting to False")
                    creds['sandbox'] = False
                    store.api_key = json_module.dumps(creds)
                    db.session.commit()
                    logging.info(f"✅ Fixed store '{store.name}' - now using live eBay API")
                elif sandbox_value is False:
                    logging.info(f"✅ Store '{store.name}' already has sandbox:false - OK")

            except Exception as store_error:
                logging.error(f"Failed to fix sandbox flag for store {store.id}: {str(store_error)}")
                try:
                    db.session.rollback()
                except:
                    pass

    except Exception as e:
        logging.error(f"Production sandbox flag fix failed (non-fatal): {str(e)}")
        try:
            db.session.rollback()
        except:
            pass

with app.app_context():
    migrate_database()
    ensure_production_ebay_sandbox_flag()

try:
    from governed_routes import governed_bp
    app.register_blueprint(governed_bp)
except Exception as exc:
    logging.error(f"Failed to register governed routes: {exc}")

try:
    from governed_group_routes import governed_group_bp
    app.register_blueprint(governed_group_bp)
except Exception as exc:
    logging.error(f"Failed to register governed group routes: {exc}")


# Import and register admin reporting blueprint
from admin_routes import admin_bp
app.register_blueprint(admin_bp)

# Governed startup checkpoint.
#
# Startup is intentionally quiet and non-executing: Flask import/module load must
# not start workers, schedulers, queue consumers, order import ticks, direct
# pushers, or marketplace API clients. Runtime execution remains disabled until
# a future approved governed command path is built.
logging.info(
    "[GOVERNED_STARTUP] Marketplace execution disabled on app boot: "
    "FBA read-only; FBM/eBay push disabled until governed path exists; "
    "no workers, schedulers, queue consumers, or marketplace API calls started."
)

# Run system events backfill on startup (automatically populates from existing logs)
with app.app_context():
    try:
        from admin_logging import run_comprehensive_backfill
        backfill_result = run_comprehensive_backfill()
        if backfill_result.get('skipped'):
            logging.info(f"System events backfill: Skipped (already has {backfill_result.get('existing_count', 0)} entries)")
        elif backfill_result.get('total'):
            logging.info(f"System events backfill: Created {backfill_result['total']} events from historical data")
        elif backfill_result.get('error'):
            logging.warning(f"System events backfill error: {backfill_result['error']}")
    except Exception as e:
        logging.warning(f"System events backfill skipped: {str(e)}")

# [STAGING SAFETY] Print environment configuration at startup
def get_db_host_safe():
    """Extract just the host from DATABASE_URL (no credentials)."""
    try:
        db_url = os.environ.get("DATABASE_URL", "")
        if "@" in db_url and "/" in db_url:
            # Format: postgres://user:pass@host:port/dbname
            after_at = db_url.split("@", 1)[1]
            host_part = after_at.split("/", 1)[0]
            return host_part
        return "unknown"
    except:
        return "unknown"


def get_db_fingerprint_hash():
    """Generate a short hash of DATABASE_URL for fingerprinting (NEVER expose full URL)."""
    import hashlib
    try:
        db_url = os.environ.get("DATABASE_URL", "")
        if not db_url:
            return "no-db-url"
        return hashlib.sha256(db_url.encode()).hexdigest()[:12]
    except:
        return "error"


import socket
try:
    HOSTNAME = socket.gethostname()
except:
    HOSTNAME = "unknown"

print("\n" + "="*60)
print("SENTINEL ENVIRONMENT FINGERPRINT")
print("="*60)
print(f"  APP_ENV:        {APP_ENV.upper()}")
print(f"  HOSTNAME:       {HOSTNAME}")
print(f"  PUSH_ENABLED:   {PUSH_ENABLED}")
print(f"  EXECUTION_MODE: {EXECUTION_MODE}")
print(f"  SENTINEL_MODE:  {SENTINEL_MODE}")
print("")
print("DATABASE:")
print(f"  HOST:           {get_db_host_safe()}")
print(f"  FINGERPRINT:    {get_db_fingerprint_hash()}")
print("")
print("SESSION ISOLATION:")
print(f"  COOKIE_NAME:    {app.config['SESSION_COOKIE_NAME']}")
print(f"  COOKIE_DOMAIN:  {app.config['SESSION_COOKIE_DOMAIN'] or '(dynamic per-request)'}")
print(f"  COOKIE_SECURE:  {app.config['SESSION_COOKIE_SECURE']}")
print(f"  SAMESITE:       {app.config['SESSION_COOKIE_SAMESITE']}")
print("")
print("SENTINEL-2 CONTROLS:")
print(f"  MODE:           {SENTINEL_MODE}")
print(f"  CMD_INPUT:      {'ENABLED' if SENTINEL_MODE == 'PLAN' else 'DISABLED'}")
print(f"  KNOWLEDGE:      {'ENABLED' if SENTINEL_MODE in ['OBSERVE', 'PLAN'] else 'DISABLED'}")
if IS_STAGING:
    print("")
    print("  ⚠️  STAGING MODE ACTIVE")
    print("  ⚠️  Push/write operations are BLOCKED by default")
print("="*60)

# Startup marketplace safety report.
# This is informational only: it performs no imports/calls to marketplace service
# clients and starts no workers, schedulers, queue consumers, or push loops.
print("\n" + "="*60)
print("MARKETPLACE STARTUP SAFETY — SHUTDOWN ONLY")
print("="*60)
print(f"Environment: {APP_ENV.upper()}")
print(f"Admin Dashboard: /admin/system-activity")
print("Runtime status:")
print("  [OK] No marketplace execution starts on app boot")
print("  [OK] No workers, schedulers, queue consumers, or order-import ticks start on app boot")
print("  [OK] FBA/AFN is read-only; no FBA push path is started")
print("  [OK] FBM/MFN push is disabled until the governed path exists")
print("  [OK] eBay push/import is disabled until the governed path exists")
print("  [OK] Amazon/eBay API error tables remain reporting-only at startup")
print("  [OK] System Activity remains available for audit/reporting")
print("="*60 + "\n")

# =========================

# =========================
# REAL LOCAL SYNC ROUTE
# =========================
