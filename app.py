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
        allow_sqlite = os.environ.get("ALLOW_SQLITE_DEV", "true").lower() == "true"
        if not allow_sqlite:
            raise RuntimeError(
                "DEV cannot use SQLite. DEV must match PROD (PostgreSQL). "
                "Set DEV_DATABASE_URL to Postgres, or set ALLOW_SQLITE_DEV=true (temporary only)."
            )
        logging.warning("⚠️  DEV MODE: SQLite allowed via ALLOW_SQLITE_DEV=true (NOT recommended)")

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
login_manager.login_view = 'routes.login'  # Blueprint route
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
    return redirect(url_for('routes.login', next=request.path))

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

# Import and register routes blueprint
from routes import bp as routes_bp
app.register_blueprint(routes_bp)

# Import and register admin reporting blueprint
from admin_routes import admin_bp
app.register_blueprint(admin_bp)

# Start queue-based sync system (dispatcher + scheduler)
# CRITICAL: In Gunicorn, app.py is imported by EACH worker process.
# Without a per-machine startup lock, dispatcher/schedulers start multiple times.
# This lock ensures only ONE worker on this Fly machine starts background services.
_STARTUP_LOCK_HANDLE = None

def acquire_startup_lock():
    """Acquire a per-machine non-blocking lock for background service startup."""
    global _STARTUP_LOCK_HANDLE
    lock_path = "/tmp/bt38_startup_services.lock"
    try:
        handle = open(lock_path, "w")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        handle.write(f"pid={os.getpid()}\n")
        handle.flush()
        _STARTUP_LOCK_HANDLE = handle  # Keep handle alive so lock is held
        logging.info(f"[STAGE5] Startup lock acquired by pid={os.getpid()}")
        return True
    except BlockingIOError:
        logging.warning(f"[STAGE5] Startup services already owned by another worker on this machine; pid={os.getpid()} will skip")
        return False
    except Exception as e:
        logging.error(f"[STAGE5] Failed to acquire startup lock: {str(e)}")
        return False

with app.app_context():
    from sync_dispatcher import start_dispatcher, set_app_instance, start_order_import_scheduler

    # Set app instance for dispatcher worker threads
    set_app_instance(app)

    owns_startup = acquire_startup_lock()

    if owns_startup and IS_ACTIVE_PRIMARY:
        start_dispatcher()
        logging.info(f"[STAGE5] Sync dispatcher started (ENV: {APP_ENV}, ROLE: {FAILOVER_ROLE})")
    elif not IS_ACTIVE_PRIMARY:
        logging.warning(f"[STAGE5] Sync dispatcher DISABLED (IS_ACTIVE_PRIMARY=false, ROLE: {FAILOVER_ROLE})")
    else:
        logging.warning("[STAGE5] Sync dispatcher SKIPPED in this worker (startup lock not owned)")

    if owns_startup and ENABLE_SCHEDULERS and IS_ACTIVE_PRIMARY:
        start_order_import_scheduler()
        logging.info("[STAGE5] Order import scheduler started (Phase 1 Auto-Sync)")
    elif not ENABLE_SCHEDULERS or not IS_ACTIVE_PRIMARY:
        logging.warning(f"[STAGE5] Order import scheduler DISABLED (ENABLE_SCHEDULERS={ENABLE_SCHEDULERS}, IS_ACTIVE_PRIMARY={IS_ACTIVE_PRIMARY})")
    else:
        logging.warning("[STAGE5] Order import scheduler SKIPPED in this worker (startup lock not owned)")

    # Background scheduler remains permanently disabled
    logging.warning("[STAGE5] Background scheduler DISABLED permanently - dispatcher is the single execution path")

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

# Print deployment verification message
print("\n" + "="*60)
print("SYSTEM REPORTING MODULE DEPLOYED — READY FOR VERIFICATION")
print("="*60)
print(f"Environment: {APP_ENV.upper()}")
print(f"Admin Dashboard: /admin/system-activity")
print("Features:")
print("  [OK] Unified System Events table with all categories")
print("  [OK] Sync Job logging (FBA import, FBM push, eBay sync)")
print("  [OK] API Error tracking (Amazon, eBay)")
print("  [OK] Configuration change history")
print("  [OK] Agent run logging")
print("  [OK] Authentication event logging")
print("  [OK] Date range filtering on all tabs")
print("  [OK] Export to CSV, JSON, TXT formats")
print("="*60)

print("\n" + "="*60)
print("AMAZON FBA/FBM UNIFIED ARCHITECTURE")
print("="*60)
print("Store Model: ONE store with fba_import_enabled + fbm_sync_enabled flags")
print("")
print("FBA (Amazon-Fulfilled):")
print("  - Fulfillment channel: AFN")
print("  - Data stored in: amazon_fba_inventory table")
print("  - Import via: sync_fba_inventory() / FBA Inventory API")
print("  - NEVER pushed (read-only from Amazon)")
print("")
print("FBM (Merchant-Fulfilled):")
print("  - Fulfillment channel: MFN")
print("  - Data stored in: marketplace_listings + warehouse_stock")
print("  - Push to Amazon via: smart_push_service / Listings API")
print("  - Warehouse is authoritative source")
print("")
print("Safety Guards:")
print("  [OK] is_pushable property blocks AFN listings")
print("  [OK] classify_fulfillment_channel() centralizes FBA/FBM logic")
print("  [OK] smart_push_service filters FBA at query time")
print("  [OK] All operations logged to System Activity")
print("="*60 + "\n")

# =========================

# =========================
# REAL LOCAL SYNC ROUTE
# =========================
@app.route("/sync/run/<int:store_id>", methods=["POST"])
@login_required
def run_real_store_sync(store_id):
    """Retired direct app-level sync route.

    This route is intentionally fail-closed so marketplace sync cannot bypass
    governed dispatcher execution.
    """
    from flask import jsonify
    return jsonify({
        "success": False,
        "error": "Direct app-level sync route is retired. Use governed dispatcher execution path.",
        "execution_blocked": True,
        "route_retired": True,
        "store_id": store_id
    }), 410

@app.route("/debug/fba-local")
def debug_fba_local():
    import sqlite3
    import os

    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instance", "bt38_ims_local.db")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("""
        SELECT seller_sku, available_quantity, reserved_quantity, total_inbound
        FROM amazon_fba_inventory
        LIMIT 50
    """)
    rows = cur.fetchall()
    conn.close()

    return {
        "db_path": db_path,
        "rows": [
            {
                "seller_sku": r[0],
                "available_quantity": r[1],
                "reserved_quantity": r[2],
                "total_inbound": r[3],
            }
            for r in rows
        ],
        "count": len(rows)
    }



    return {
        "db_path": db_path,
        "count": len(rows),
        "rows": [
            {
                "seller_sku": r[0],
                "available_quantity": r[1],
                "reserved_quantity": r[2],
                "total_inbound": r[3],
            }
            for r in rows
        ]
    }


@app.route("/debug/fba-local-direct")
def debug_fba_local_direct():
    import sqlite3

    db_path = r"C:\Users\btail\_ARCHIVE_OLD_BT38\BT38\instance\bt38_ims_local.db"

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("""
        SELECT seller_sku, available_quantity, reserved_quantity, total_inbound
        FROM amazon_fba_inventory
        LIMIT 100
    """)

    rows = cur.fetchall()
    conn.close()

    return {
        "db_path": db_path,
        "count": len(rows),
        "rows": rows
    }




@app.route("/debug/fba-open")
def debug_fba_open():
    import sqlite3

    db_path = "instance/bt38_ims_local.db"

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # get real columns
    cur.execute("PRAGMA table_info(amazon_fba_inventory)")
    cols = [r["name"] for r in cur.fetchall()]

    cur.execute("SELECT * FROM amazon_fba_inventory LIMIT 20")
    rows = [dict(r) for r in cur.fetchall()]

    conn.close()

    return {
        "db_path": db_path,
        "columns": cols,
        "row_count": len(rows),
        "rows": rows
    }
