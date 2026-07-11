from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
import json

from flask import Blueprint, jsonify, request, render_template, redirect, url_for
try:
    from flask_login import current_user, login_required
except Exception:
    current_user = None

    def login_required(f):
        return f

governed_bp = Blueprint("governed", __name__)

@governed_bp.route("/logout")
@login_required
def logout():
    from flask import redirect, url_for, session

    try:
        session.clear()
    except Exception:
        pass

    if logout_user:
        logout_user()

    response = redirect(url_for("governed.login"))

    response.delete_cookie("bt38_session_prod")

    return response



def _governed_json_safe(value):
    """Convert governed results to JSON-safe values before jsonify."""
    from datetime import date, datetime
    from decimal import Decimal

    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if isinstance(value, dict):
        safe = {}
        for key, item in value.items():
            if key in {"store", "listing", "_governed_store", "_governed_listing"}:
                if item is None:
                    safe[key] = None
                else:
                    safe[key] = {
                        "id": getattr(item, "id", None),
                        "name": getattr(item, "name", None),
                        "platform": getattr(item, "platform", None),
                        "sku": getattr(item, "external_sku", None) or getattr(item, "sku", None),
                        "store_id": getattr(item, "store_id", None),
                    }
            else:
                safe[str(key)] = _governed_json_safe(item)
        return safe

    if isinstance(value, (list, tuple, set)):
        return [_governed_json_safe(item) for item in value]

    return str(value)


@governed_bp.get("/")
def governed_root_page():
    return redirect(url_for("governed.governed_dashboard_page"))



@governed_bp.get("/dashboard")
@login_required
def governed_dashboard_page():
    """Human health dashboard.

    One clear path:
    existing governed sources -> attention summary -> dashboard.

    The dashboard does not call marketplaces, start sync, push stock, import
    orders, or create a second notification system.
    """
    import json as _json
    from types import SimpleNamespace
    from models import Store, SystemLog, MarketplaceOrder, SalesOrder, SalesOrderItem, MCFOrder

    stores = Store.query.order_by(Store.id).all()

    webhook_logs = (
        SystemLog.query
        .filter(SystemLog.log_type == "marketplace_webhook")
        .order_by(SystemLog.created_at.desc())
        .limit(12)
        .all()
    )

    marketplace_orders = (
        MarketplaceOrder.query
        .order_by(MarketplaceOrder.created_at.desc())
        .limit(50)
        .all()
    )

    sales_orders = (
        SalesOrder.query
        .order_by(SalesOrder.created_at.desc())
        .limit(50)
        .all()
    )

    sales_order_items = (
        SalesOrderItem.query
        .order_by(SalesOrderItem.created_at.desc())
        .limit(50)
        .all()
    )

    mcf_orders = (
        MCFOrder.query
        .order_by(MCFOrder.created_at.desc())
        .limit(50)
        .all()
    )

    attention_items = []

    def _platform_link(platform: str, action_type: str) -> str:
        p = (platform or "").lower()
        a = (action_type or "").lower()

        if "ebay" in p:
            if "dispatch" in a or "order" in a:
                return "https://www.ebay.co.uk/sh/ord"
            if "message" in a or "buyer" in a:
                return "https://www.ebay.co.uk/sh/messages"
            return "https://www.ebay.co.uk/sh/overview"

        if "amazon" in p:
            if "dispatch" in a or "order" in a:
                return "https://sellercentral.amazon.co.uk/orders-v3"
            if "message" in a or "buyer" in a:
                return "https://sellercentral.amazon.co.uk/messaging"
            return "https://sellercentral.amazon.co.uk/home"

        return "/dashboard"

    def _human_title(platform: str, event_type: str) -> tuple[str, str]:
        text = (event_type or "").replace("_", " ").replace("-", " ").strip().lower()
        platform_name = (platform or "Marketplace").title()

        if "message" in text or "buyer" in text or "inquiry" in text:
            return "Buyer message waiting", f"{platform_name} customer message needs a reply."

        if "dispatch" in text or "ship" in text or "fulfillment" in text or "order" in text:
            return "Order waiting to dispatch", f"{platform_name} order needs dispatch attention."

        if "auth" in text or "disconnect" in text or "token" in text:
            return "Marketplace connection needs attention", f"{platform_name} connection may need reconnecting."

        if "listing" in text or "blocked" in text or "policy" in text:
            return "Listing needs attention", f"{platform_name} listing needs review."

        return "Marketplace update received", f"{platform_name} has sent a notification."

    real_sales_count = len(marketplace_orders) + len(sales_orders)
    real_dispatch_pending = 0
    real_mcf_pending = 0

    for order in marketplace_orders:
        status = (order.status or "").strip().lower()
        fulfillment = (order.fulfillment_type or "FBM").strip().upper()
        is_shipped = bool(order.shipped_at)

        if status in {"pending", "new", "unshipped", "awaiting_dispatch", "processing"} or not is_shipped:
            real_dispatch_pending += 1
            platform = order.store.platform if order.store else "Marketplace"
            store_name = order.store.name if order.store else "Marketplace"
            title = "Order waiting to dispatch"
            if fulfillment == "FBA":
                title = "FBA / MCF order needs attention"

            attention_items.append(SimpleNamespace(
                source="marketplace_order",
                marketplace=(platform or "Marketplace").title(),
                store_name=store_name,
                title=title,
                message=f"{store_name} order {order.marketplace_order_id} needs attention for SKU {order.sku} x{order.quantity}.",
                status=order.status or "pending",
                reason=order.error_message or "",
                severity="warning",
                action_url=(
                    "https://www.ebay.co.uk/sh/ord"
                    if "ebay" in (platform or "").lower()
                    else "https://sellercentral.amazon.co.uk/orders-v3"
                    if "amazon" in (platform or "").lower()
                    else "/dashboard"
                ),
                action_label=f"Open {(platform or 'Marketplace').title()}",
                created_at=order.created_at,
            ))

    for order in sales_orders:
        status = (order.status or "").strip().lower()
        if status in {"draft", "pending", "confirmed", "processing", "unfulfilled"} or not order.ship_date:
            real_dispatch_pending += 1
            attention_items.append(SimpleNamespace(
                source="sales_order",
                marketplace="Sales",
                store_name="Sales Orders",
                title="Sales order waiting to fulfil",
                message=f"Sales order {order.order_number} needs fulfilment attention.",
                status=order.status or "pending",
                reason="",
                severity="warning",
                action_url=url_for("governed.governed_dashboard_page"),
                action_label="Open dashboard",
                created_at=order.created_at,
            ))

    for item in sales_order_items:
        if not item.is_fulfilled:
            attention_items.append(SimpleNamespace(
                source="sales_order_item",
                marketplace="Sales",
                store_name="Sales Orders",
                title="Order item not fulfilled",
                message=f"SKU {item.sku or 'Unknown'} has {item.quantity or 0} unit(s) not fulfilled.",
                status="not_fulfilled",
                reason="",
                severity="warning",
                action_url=url_for("governed.governed_dashboard_page"),
                action_label="Review",
                created_at=item.created_at,
            ))

    for order in mcf_orders:
        status = (order.status or "").strip().lower()
        amazon_status = (order.amazon_status or "").strip().lower()
        if status in {"pending", "failed", "error", "processing"} or amazon_status in {"pending", "failed", "error"}:
            real_mcf_pending += 1
            attention_items.append(SimpleNamespace(
                source="mcf_order",
                marketplace=(order.source_channel or "MCF").title(),
                store_name="Amazon MCF",
                title="MCF fulfilment needs attention",
                message=f"MCF order {order.seller_fulfillment_order_id} is {order.status or 'pending'}.",
                status=order.status or "pending",
                reason=order.last_error or "",
                severity="warning",
                action_url="https://sellercentral.amazon.co.uk/orders-v3",
                action_label="Open Amazon",
                created_at=order.created_at,
            ))

    for log in webhook_logs:
        try:
            details = _json.loads(log.details or "{}")
        except Exception:
            details = {}

        platform = details.get("marketplace") or "marketplace"
        event_type = details.get("event_type") or "marketplace_notification"
        status = details.get("status") or "received"
        reason = details.get("reason") or ""
        payload = details.get("payload") or {}

        title, message = _human_title(platform, event_type)
        action_url = (
            payload.get("action_url")
            or payload.get("external_url")
            or payload.get("url")
            or _platform_link(platform, event_type)
        )

        attention_items.append(SimpleNamespace(
            source="webhook",
            marketplace=platform.title(),
            store_name=details.get("store_name") or "Marketplace",
            title=title,
            message=message,
            status=status,
            reason=reason,
            severity="info" if status == "received" else "muted",
            action_url=action_url,
            action_label=f"Open {platform.title()}",
            created_at=log.created_at,
        ))

    for store in stores:
        auth_status = (store.auth_status or "ok").lower()
        if auth_status and auth_status != "ok":
            platform = store.platform or "Marketplace"
            attention_items.append(SimpleNamespace(
                source="store",
                marketplace=platform.title(),
                store_name=store.name,
                title="Store connection needs attention",
                message=store.auth_error_message or f"{store.name} may need reconnecting.",
                status=auth_status,
                reason=store.auth_error_code or "auth_status",
                severity="warning",
                action_url="/settings",
                action_label="Open settings",
                created_at=store.auth_error_at or store.updated_at,
            ))

        if (store.sync_status or "").lower() in {"error", "failed"}:
            platform = store.platform or "Marketplace"
            attention_items.append(SimpleNamespace(
                source="store",
                marketplace=platform.title(),
                store_name=store.name,
                title="Marketplace action needs attention",
                message=f"{store.name} has a marketplace status of {store.sync_status}.",
                status=store.sync_status,
                reason=store.pause_reason or "",
                severity="warning",
                action_url="/settings",
                action_label="Open settings",
                created_at=store.updated_at,
            ))

    pending_messages = sum(
        1 for item in attention_items
        if "message" in (item.title or "").lower()
    )
    pending_dispatch = sum(
        1 for item in attention_items
        if "dispatch" in (item.title or "").lower()
    )

    dashboard_stats = {
        "total_items": 0,
        "active_stores": sum(1 for store in stores if store.is_active),
        "total_stores": len(stores),
        "low_stock_items": 0,
        "total_attention": len(attention_items),
        "pending_messages": pending_messages,
        "pending_dispatch": real_dispatch_pending,
        "real_sales_count": real_sales_count,
        "real_mcf_pending": real_mcf_pending,
    }

    return render_template(
        "dashboard.html",
        stats=dashboard_stats,
        attention_items=attention_items[:12],
        stores=stores,
    )


@governed_bp.get("/stores")
def governed_stores_page():
    from models import Store

    stores = Store.query.order_by(Store.id).all()
    return render_template("stores.html", stores=stores)


@governed_bp.post("/governed/stores/<int:store_id>/toggle")
def governed_store_toggle(store_id):
    from extensions import db
    from models import Store

    store = Store.query.get_or_404(store_id)
    body = request.get_json(silent=True) or {}
    store.is_active = bool(body.get("is_active"))
    db.session.commit()

    return jsonify({
        "ok": True,
        "success": True,
        "store_id": store.id,
        "is_active": store.is_active,
        "message": "Store active state updated through governed path."
    })


@governed_bp.post("/governed/stores/<int:store_id>/sync-preview")
def governed_store_sync_preview(store_id):
    from models import Store

    store = Store.query.get_or_404(store_id)
    return jsonify({
        "ok": True,
        "success": True,
        "store_id": store.id,
        "store_name": store.name,
        "platform": store.platform,
        "message": "Preview only. No marketplace sync was executed.",
        "governed": True
    })


@governed_bp.post("/governed/stores/<int:store_id>/delete-preview")
def governed_store_delete_preview(store_id):
    from models import Store

    store = Store.query.get_or_404(store_id)
    return jsonify({
        "ok": False,
        "success": False,
        "store_id": store.id,
        "store_name": store.name,
        "message": "Store deletion is disabled in governed mode until delete rules are approved.",
        "governed": True
    })


@governed_bp.get("/governed/stores/amazon/setup-preview")
def governed_amazon_setup_preview():
    return jsonify({
        "ok": True,
        "success": True,
        "message": "Amazon setup preview only. Live credential setup is not wired through old routes.",
        "governed": True
    })


@governed_bp.get("/settings")
def governed_settings_page():
    from flask import render_template
    from models import Store, SystemConfig

    default_config = {
        "push_enabled": "false",
        "runtime_push_enabled": "false",
        "marketplace_push_enabled": "false",
        "import_enabled": "false",
        "runtime_import_enabled": "false",
        "marketplace_import_enabled": "false",
        "sync_enabled": "false",
        "runtime_sync_enabled": "false",
        "marketplace_sync_enabled": "false",
        "manual_push_enabled": "false",
        "manual_import_enabled": "false",
        "manual_sync_enabled": "false",
        "quantity_push_enabled": "false",
        "price_push_enabled": "false",
        "group_push_enabled": "false",
        "bulk_push_enabled": "false",
        "read_only_mode": "false",
        "dry_run_mode": "false",
        "queue_frozen": "false",
        "scheduler_enabled": "false",
        "sync_worker_enabled": "false",
        "push_worker_enabled": "false",
        "retry_queue_enabled": "false",
        "reconcile_15m_enabled": "false",
        "webhook_worker_enabled": "false",
        "webhook_ebay_enabled": "false",
        "webhook_amazon_enabled": "false",
        "default_push_frequency_minutes": "15",
        "default_batch_size": "25",
        "default_retry_attempts": "3",
        "api_rate_limit_buffer": "0.8",
        "error_rate_threshold": "0.3",
    }

    config = dict(default_config)

    rows = SystemConfig.query.filter(
        SystemConfig.key.in_(list(default_config.keys()))
    ).all()

    for row in rows:
        config[row.key] = str(row.value)

    def _on(key):
        return str(config.get(key, "false")).strip().lower() in {"1", "true", "yes", "on"}

    def _status(action, required):
        if _on("read_only_mode"):
            return {"label": "BLOCKED", "reason": "read_only_mode is ON", "required": {k: _on(k) for k in required}}
        for key in required:
            if not _on(key):
                return {"label": "BLOCKED", "reason": f"{key} is OFF", "required": {k: _on(k) for k in required}}
        return {"label": "ALLOWED", "reason": f"{action} circuit is fully powered", "required": {k: _on(k) for k in required}}

    fuse_status = {
        "push": _status("Push", ["push_enabled", "runtime_push_enabled", "marketplace_push_enabled", "manual_push_enabled"]),
        "import": _status("Import", ["import_enabled", "runtime_import_enabled", "marketplace_import_enabled", "manual_import_enabled"]),
        "sync": _status("Sync", ["sync_enabled", "runtime_sync_enabled", "marketplace_sync_enabled", "manual_sync_enabled"]),
        "global": {
            "read_only_mode": _on("read_only_mode"),
            "dry_run_mode": _on("dry_run_mode"),
            "queue_frozen": _on("queue_frozen"),
        },
    }

    class Stats:
        failed_24h = 0
        failed_syncs = 0
        success_rate = 100

    stores = Store.query.order_by(Store.id).all()

    return render_template(
        "settings.html",
        config=config,
        fuse_status=fuse_status,
        stores=stores,
        stats=Stats(),
    )


@governed_bp.get("/listings")
def governed_listings_page():
    return render_template(
        "listings.html",
        listings=[],
        groups=[],
        stats={},
        filtered_count=0,
        total_listings=0,
        active_listings=0,
        blocked_listings=0,
        current_search="",
        current_platform_filter="",
        current_store_filter="",
        current_status_filter="",
        all_stores=[]
    )


@governed_bp.get("/groups")
def governed_groups_page():
    return render_template("groups.html")


@governed_bp.get("/product-linking")
def governed_product_linking_page():
    return render_template(
        "product_linking.html",
        warehouse_products=[],
        unlinked_listings=[],
        unlinked_by_platform={},
        all_marketplace_listings=[],
        all_stores=[],
        current_search="",
        current_platform="all",
        current_store="all",
        current_show_linked="all",
        async_load=True
    )


@governed_bp.get("/inventory")
def governed_inventory_page():
    return render_template("inventory.html")



@governed_bp.route("/login", methods=["GET", "POST"])
def login():
    from datetime import datetime
    from flask_login import login_user
    from extensions import db
    from models import User

    requested_next = request.args.get("next") or request.form.get("next") or ""
    if requested_next.startswith("/") and not requested_next.startswith("//") and "\\" not in requested_next:
        next_url = requested_next
    else:
        next_url = url_for("governed.governed_warehouse_page")

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        user = (
            db.session.query(User)
            .filter((User.email == username) | (User.username == username))
            .first()
        )

        if user and user.is_active and user.check_password(password):
            user.last_login = datetime.utcnow()
            db.session.commit()
            login_user(user, remember=True)
            return redirect(next_url)

        error = "Invalid login details or inactive user."
    else:
        error = ""

    return render_template(
        "login.html",
        error=error,
        next_url=next_url,
    )


def _bt38_structure_secret_ok(payload: dict) -> bool:
    """Owner-only structure lock.

    Normal Sync All usage must not require a password.
    This only protects changing sync/fuse/store structure.
    Secret is stored only in Fly:
    BT38_SYNC_ALL_SECRET
    """
    import os

    expected = (os.environ.get("BT38_SYNC_ALL_SECRET") or "").strip()
    provided = str((payload or {}).get("structure_secret") or "").strip()

    return bool(expected and provided and provided == expected)


def _bt38_structure_lock_response():
    return jsonify({
        "ok": False,
        "success": False,
        "governed": True,
        "locked": True,
        "execution_blocked": True,
        "reason": "Structure change locked. Enter the owner password to change sync/fuse alignment.",
    }), 423


BT38_SYNC_STRUCTURE_KEYS = {
    "sync_enabled",
    "runtime_sync_enabled",
    "marketplace_sync_enabled",
    "manual_sync_enabled",
    "sync_worker_enabled",
    "scheduler_enabled",
    "reconcile_15m_enabled",
    "webhook_worker_enabled",
    "webhook_ebay_enabled",
    "webhook_amazon_enabled",
}

BT38_SYNC_STORE_FIELDS = {
    "store_mode",
    "is_active",
    "fbm_sync_enabled",
    "auto_push_enabled",
    "fba_import_enabled",
}




def _governed_admin_required(f):
    """Single admin gate for fuse-box user authority."""
    from functools import wraps

    @wraps(f)
    def decorated_function(*args, **kwargs):
        from flask import flash
        try:
            if not current_user or not current_user.is_authenticated:
                flash("Please log in to access this page.", "warning")
                return redirect(url_for("governed.login"))
            if getattr(current_user, "role", "") != "admin":
                flash("You do not have permission to access this page.", "danger")
                return redirect(url_for("governed.governed_dashboard_page"))
        except Exception:
            flash("Please log in to access this page.", "warning")
            return redirect(url_for("governed.login"))
        return f(*args, **kwargs)

    return decorated_function


USER_PERMISSION_SECTIONS = [
    "inventory",
    "warehouse",
    "stores",
    "suppliers",
    "purchase_orders",
    "sync",
    "settings",
    "users",
]


def _full_user_permissions():
    """Full owner/admin fuse-box authority.

    This is still one permission layer. It only fills the existing
    User.permissions JSON so the owner account is visible as fully aligned.
    """
    permissions = {}
    for section in USER_PERMISSION_SECTIONS:
        permissions[f"view_{section}"] = True
        permissions[f"edit_{section}"] = True

    permissions["can_push"] = True
    permissions["can_sync"] = True
    permissions["can_import"] = True
    permissions["can_manage_users"] = True
    return permissions


def _build_user_permissions_from_form(form, role="viewer"):
    """Build the existing permissions JSON from one fuse-box authority form.

    These are shortcut/authority flags only. They do not create duplicate sync,
    push, import, marketplace, or runtime paths.
    """
    if str(role or "").strip().lower() == "admin":
        return _full_user_permissions()

    permissions = {}
    for section in USER_PERMISSION_SECTIONS:
        permissions[f"view_{section}"] = form.get(f"view_{section}") == "on"
        permissions[f"edit_{section}"] = form.get(f"edit_{section}") == "on"

    permissions["can_push"] = permissions.get("edit_inventory", False) or permissions.get("edit_warehouse", False)
    permissions["can_sync"] = permissions.get("edit_sync", False)
    permissions["can_import"] = permissions.get("edit_stores", False)
    permissions["can_manage_users"] = permissions.get("edit_users", False)
    return permissions


@governed_bp.route("/users")
@_governed_admin_required
def user_management():
    from models import User

    users = User.query.order_by(User.created_at.desc(), User.id.desc()).all()
    return render_template("user_management.html", users=users)


@governed_bp.route("/users/create", methods=["GET", "POST"])
@_governed_admin_required
def create_user():
    from flask import flash
    from extensions import db
    from models import User

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        role = (request.form.get("role") or "viewer").strip().lower()

        if role not in {"viewer", "manager", "admin"}:
            role = "viewer"

        if len(password) < 6:
            flash("Password must be at least 6 characters.", "danger")
            return render_template("create_user.html")

        existing = User.query.filter((User.username == username) | (User.email == email)).first()
        if existing:
            flash("That user already exists. Opened the existing user so you can edit access.", "warning")
            return redirect(url_for("governed.edit_user", user_id=existing.id))

        user = User(username=username, email=email, role=role, permissions=_full_user_permissions() if role == "admin" else {})
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        flash("User created. You can now assign access from the edit screen.", "success")
        return redirect(url_for("governed.edit_user", user_id=user.id))

    return render_template("create_user.html")


@governed_bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@_governed_admin_required
def edit_user(user_id):
    from flask import flash
    from extensions import db
    from models import User

    user = User.query.get_or_404(user_id)

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        role = (request.form.get("role") or "viewer").strip().lower()
        password = request.form.get("password") or ""

        if role not in {"viewer", "manager", "admin"}:
            role = "viewer"

        duplicate = User.query.filter(User.email == email, User.id != user.id).first()
        if duplicate:
            flash("Another user already uses that email.", "danger")
            return render_template("edit_user.html", user=user)

        user.email = email
        user.role = role
        user.is_active = request.form.get("is_active") == "on"
        user.permissions = _build_user_permissions_from_form(request.form, role)

        if password:
            if len(password) < 6:
                flash("Password must be at least 6 characters.", "danger")
                return render_template("edit_user.html", user=user)
            user.set_password(password)

        db.session.commit()
        flash("User access updated through the fuse-box permission authority.", "success")
        return redirect(url_for("governed.user_management"))

    if user.permissions is None:
        user.permissions = {}
    return render_template("edit_user.html", user=user)


@governed_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@_governed_admin_required
def delete_user(user_id):
    from flask import flash
    from extensions import db
    from models import User

    user = User.query.get_or_404(user_id)

    if current_user and user.id == current_user.id:
        flash("You cannot deactivate your own account.", "danger")
        return redirect(url_for("governed.user_management"))

    user.is_active = False
    db.session.commit()
    flash("User deactivated.", "success")
    return redirect(url_for("governed.user_management"))


# ============================================================
# Governed marketplace webhook intake
# ============================================================
# One clear path:
# marketplace notification -> governed intake -> existing SystemLog
# No sync, push, import, adapter call, or marketplace execution happens here.
# Dashboard will later read normalized attention from governed sources only.

def _bt38_config_on(key: str) -> bool:
    from models import SystemConfig

    row = SystemConfig.query.filter_by(key=key).first()
    if not row:
        return False
    return str(row.value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _bt38_webhook_platform_allowed(platform: str) -> tuple[bool, str]:
    platform = (platform or "").strip().lower()

    if platform not in {"amazon", "ebay"}:
        return False, "unsupported_marketplace"

    if not _bt38_config_on("webhook_worker_enabled"):
        return False, "webhook_worker_enabled is OFF"

    if platform == "amazon" and not _bt38_config_on("webhook_amazon_enabled"):
        return False, "webhook_amazon_enabled is OFF"

    if platform == "ebay" and not _bt38_config_on("webhook_ebay_enabled"):
        return False, "webhook_ebay_enabled is OFF"

    return True, "allowed"


def _bt38_webhook_payload() -> dict:
    body = request.get_json(silent=True)
    if isinstance(body, dict):
        return body

    raw = request.get_data(as_text=True) or ""
    if raw:
        return {"raw": raw}

    return {}


def _bt38_match_webhook_store(platform: str, payload: dict):
    from models import Store

    store_id = request.headers.get("X-BT38-Store-ID") or payload.get("store_id")
    if store_id:
        try:
            return Store.query.get(int(store_id))
        except Exception:
            return None

    platform_like = f"%{platform}%"
    return (
        Store.query
        .filter(Store.platform.ilike(platform_like))
        .filter(Store.is_active == True)  # noqa: E712
        .order_by(Store.id)
        .first()
    )


def _bt38_record_webhook_event(platform: str, status: str, reason: str, payload: dict):
    from extensions import db
    from models import SystemLog

    store = _bt38_match_webhook_store(platform, payload)

    event_type = (
        payload.get("event_type")
        or payload.get("notificationType")
        or payload.get("topic")
        or payload.get("type")
        or "marketplace_notification"
    )

    details = {
        "governed": True,
        "source": "governed_webhook_intake",
        "marketplace": platform,
        "store_id": getattr(store, "id", None),
        "store_name": getattr(store, "name", None),
        "event_type": event_type,
        "status": status,
        "reason": reason,
        "headers": {
            "user_agent": request.headers.get("User-Agent"),
            "content_type": request.headers.get("Content-Type"),
            "x_bt38_store_id": request.headers.get("X-BT38-Store-ID"),
        },
        "payload": payload,
    }

    row = SystemLog(
        log_type="marketplace_webhook",
        message=f"{platform} webhook {status}: {event_type}",
        details=json.dumps(details, default=str),
    )
    db.session.add(row)
    db.session.commit()
    return row


@governed_bp.route("/governed/webhooks/<marketplace>", methods=["GET", "POST"])
def governed_marketplace_webhook_intake(marketplace):
    platform = (marketplace or "").strip().lower()

    if platform not in {"amazon", "ebay"}:
        return jsonify({
            "ok": False,
            "success": False,
            "governed": True,
            "error": "unsupported_marketplace",
            "marketplace": platform,
        }), 404

    # Lightweight challenge echo only. Provider-specific verification can be
    # added later after the exact marketplace challenge contract is audited.
    if request.method == "GET":
        challenge = (
            request.args.get("challenge_code")
            or request.args.get("challenge")
            or request.args.get("hub.challenge")
        )
        return jsonify({
            "ok": True,
            "success": True,
            "governed": True,
            "marketplace": platform,
            "challenge": challenge,
            "message": "Governed webhook intake is reachable. No marketplace execution was run.",
        }), 200

    payload = _bt38_webhook_payload()
    allowed, reason = _bt38_webhook_platform_allowed(platform)
    status = "received" if allowed else "blocked_by_fuse"

    row = _bt38_record_webhook_event(
        platform=platform,
        status=status,
        reason=reason,
        payload=payload,
    )

    notification_result = None
    if allowed:
        from services.governed_webhook_execution import process_marketplace_notification
        notification_result = process_marketplace_notification(
            marketplace=platform,
            payload=payload,
            actor=f"webhook_{platform}",
        )

    return jsonify({
        "ok": True,
        "success": True,
        "governed": True,
        "marketplace": platform,
        "status": status,
        "reason": reason,
        "system_log_id": row.id,
        "notification_result": _governed_json_safe(notification_result),
        "message": "Webhook notification stored and routed through governed notification bridge when fuses allow it.",
    }), 200



@governed_bp.get("/governed/audit/notifications")
@login_required
def governed_notification_audit():
    """Read-only notification audit.

    No sync.
    No push.
    No marketplace call.
    No DB write.

    Shows what marketplace notifications and governed webhook execution records
    have actually reached Neon.
    """
    from extensions import db
    from models import SystemLog

    try:
        limit = int(request.args.get("limit") or 100)
    except Exception:
        limit = 100
    limit = max(1, min(limit, 500))

    rows = (
        db.session.query(SystemLog)
        .filter(SystemLog.log_type.in_(["marketplace_webhook", "governed_webhook_execution"]))
        .order_by(SystemLog.created_at.desc(), SystemLog.id.desc())
        .limit(limit)
        .all()
    )

    records = []
    for row in rows:
        records.append({
            "id": row.id,
            "log_type": row.log_type,
            "message": row.message,
            "details": row.details,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        })

    return jsonify({
        "ok": True,
        "success": True,
        "governed": True,
        "read_only": True,
        "source": "Neon SystemLog",
        "limit": limit,
        "count": len(records),
        "records": records,
    }), 200



@governed_bp.get("/governed/audit/notification-match")
@login_required
def governed_notification_match_preview():
    """Read-only notification match preview.

    No sync.
    No push.
    No marketplace call.
    No DB write.
    No stock change.

    Shows which MarketplaceListing, WarehouseStock, and group a future
    notification payload would match.
    """
    from extensions import db
    from models import MarketplaceListing

    listing_id = request.args.get("listing_id")
    external_listing_id = request.args.get("external_listing_id")
    sku = request.args.get("sku")
    marketplace = (request.args.get("marketplace") or "").strip().lower()

    query = db.session.query(MarketplaceListing)

    if listing_id:
        try:
            query = query.filter(MarketplaceListing.id == int(listing_id))
        except Exception:
            return jsonify(ok=False, success=False, governed=True, read_only=True, error="Invalid listing_id"), 400
    elif external_listing_id:
        query = query.filter(MarketplaceListing.external_listing_id == str(external_listing_id))
    elif sku:
        query = query.filter(MarketplaceListing.external_sku == str(sku))
    else:
        return jsonify({
            "ok": False,
            "success": False,
            "governed": True,
            "read_only": True,
            "error": "Provide listing_id, external_listing_id, or sku",
        }), 400

    if marketplace:
        query = query.join(MarketplaceListing.store).filter_by(platform=marketplace)

    listing = query.order_by(MarketplaceListing.id.asc()).first()

    if not listing:
        return jsonify({
            "ok": True,
            "success": True,
            "governed": True,
            "read_only": True,
            "matched": False,
            "message": "No MarketplaceListing matched this preview input.",
            "input": {
                "listing_id": listing_id,
                "external_listing_id": external_listing_id,
                "sku": sku,
                "marketplace": marketplace,
            },
        }), 200

    stock = listing.warehouse_stock
    group_id = (
        getattr(listing, "master_product_group_id", None)
        or getattr(stock, "master_product_group_id", None) if stock else None
    )

    linked_group_count = 0
    linked_stock_count = 0

    if group_id:
        linked_group_count = (
            db.session.query(MarketplaceListing.id)
            .filter(MarketplaceListing.master_product_group_id == int(group_id))
            .filter(MarketplaceListing.is_active == True)  # noqa: E712
            .count()
        )

    if stock:
        linked_stock_count = (
            db.session.query(MarketplaceListing.id)
            .filter(MarketplaceListing.warehouse_stock_id == int(stock.id))
            .filter(MarketplaceListing.is_active == True)  # noqa: E712
            .count()
        )

    grouped = bool(
        group_id
        or (bool(getattr(stock, "is_group_controlled", False)) if stock else False)
        or linked_group_count > 1
        or linked_stock_count > 1
    )

    return jsonify({
        "ok": True,
        "success": True,
        "governed": True,
        "read_only": True,
        "matched": True,
        "authority": "database_relationship_state",
        "route_would_be": "group" if grouped else "warehouse",
        "listing": {
            "id": listing.id,
            "store_id": listing.store_id,
            "platform": getattr(listing.store, "platform", None) if listing.store else None,
            "store_name": getattr(listing.store, "name", None) if listing.store else None,
            "external_sku": listing.external_sku,
            "external_listing_id": listing.external_listing_id,
            "title": getattr(listing, "title", None),
            "master_product_group_id": listing.master_product_group_id,
            "warehouse_stock_id": listing.warehouse_stock_id,
        },
        "warehouse": None if not stock else {
            "id": stock.id,
            "sku": stock.sku,
            "title": getattr(stock, "product_name", None) or getattr(stock, "name", None),
            "available_quantity": stock.available_quantity,
            "sellable_quantity": stock.sellable_quantity,
            "master_product_group_id": stock.master_product_group_id,
            "is_group_controlled": bool(stock.is_group_controlled),
        },
        "group": {
            "grouped": grouped,
            "group_id": int(group_id) if group_id else None,
            "linked_group_count": linked_group_count,
            "linked_stock_count": linked_stock_count,
        },
    }), 200



@governed_bp.get("/governed/audit/order-authority")
@login_required
def governed_order_authority_audit():
    """Read-only order authority audit.

    No sync.
    No push.
    No marketplace call.
    No DB write.

    Shows which order tables currently contain records so BT38 can identify
    the true order authority path.
    """
    from extensions import db
    from models import MarketplaceOrder, CanonicalOrderLine, SalesOrder, SalesOrderItem, MCFOrder

    def safe_count(model):
        try:
            return db.session.query(model).count()
        except Exception as exc:
            return {"error": str(exc)}

    def latest(model, fields, limit=10):
        try:
            rows = db.session.query(model).order_by(model.id.desc()).limit(limit).all()
            output = []
            for row in rows:
                item = {}
                for field in fields:
                    value = getattr(row, field, None)
                    if hasattr(value, "isoformat"):
                        value = value.isoformat()
                    item[field] = value
                output.append(item)
            return output
        except Exception as exc:
            return [{"error": str(exc)}]

    return jsonify({
        "ok": True,
        "success": True,
        "governed": True,
        "read_only": True,
        "counts": {
            "MarketplaceOrder": safe_count(MarketplaceOrder),
            "CanonicalOrderLine": safe_count(CanonicalOrderLine),
            "SalesOrder": safe_count(SalesOrder),
            "SalesOrderItem": safe_count(SalesOrderItem),
            "MCFOrder": safe_count(MCFOrder),
        },
        "latest": {
            "MarketplaceOrder": latest(MarketplaceOrder, [
                "id", "store_id", "marketplace_order_id", "marketplace_order_item_id",
                "sku", "warehouse_stock_id", "quantity", "carrier", "tracking_number",
                "status", "created_at", "processed_at"
            ]),
            "CanonicalOrderLine": latest(CanonicalOrderLine, [
                "id", "platform", "external_order_id", "settlement_id", "sku",
                "quantity", "gross_amount", "total_amount", "status", "created_at"
            ]),
            "SalesOrder": latest(SalesOrder, [
                "id", "order_number", "status", "created_at", "ship_date"
            ]),
            "SalesOrderItem": latest(SalesOrderItem, [
                "id", "order_id", "sku", "quantity", "is_fulfilled", "created_at"
            ]),
            "MCFOrder": latest(MCFOrder, [
                "id", "source_channel", "source_order_id", "seller_fulfillment_order_id",
                "amazon_order_id", "status", "amazon_status", "carrier",
                "tracking_number", "created_at"
            ]),
        },
    }), 200


@governed_bp.get("/shutdown-proof/status")
def shutdown_proof_status():
    return jsonify({
        "success": True,
        "ok": True,
        "shutdown_mode": True,
        "old_marketplace_routes_present": False,
    })


@governed_bp.get("/warehouse")
@login_required
def governed_warehouse_page():
    """Governed Master Stock UI.

    Speed-safe route:
    - no marketplace execution
    - no old routes
    - eager-loads relationships to avoid N+1 queries
    - limits initial render size
    """
    from extensions import db
    from models import MarketplaceListing, WarehouseStock, Store, AmazonFBAInventory
    from sqlalchemy import or_
    from sqlalchemy.orm import joinedload

    q = (request.args.get("q") or "").strip().lower()
    view = (request.args.get("view") or "all").strip().lower()

    try:
        row_limit = int(request.args.get("per_page") or 15)
    except Exception:
        row_limit = 15

    if row_limit not in (15, 25, 50, 100):
        row_limit = 15

    try:
        page = int(request.args.get("page") or 1)
    except Exception:
        page = 1
    page = max(page, 1)

    listing_query = (
        db.session.query(MarketplaceListing)
        .options(
            joinedload(MarketplaceListing.store),
            joinedload(MarketplaceListing.warehouse_stock),
        )
        .filter(MarketplaceListing.is_active == True)  # noqa: E712
        # FBA Read Only quantity is shown as a shortcut from AmazonFBAInventory.
        # Do not show generated "Amazon SKU ..." shadow rows as separate Master Stock listings.
        .filter(~MarketplaceListing.title.ilike("Amazon SKU%"))
    )

    if q:
        like = f"%{q}%"
        listing_query = listing_query.filter(
            or_(
                MarketplaceListing.external_sku.ilike(like),
                MarketplaceListing.title.ilike(like),
                MarketplaceListing.external_listing_id.ilike(like),
                MarketplaceListing.parent_item_id.ilike(like),
                MarketplaceListing.external_parent_id.ilike(like),
                MarketplaceListing.variation_sku_map.ilike(like),
                MarketplaceListing.asin.ilike(like),
                MarketplaceListing.fnsku.ilike(like),
                MarketplaceListing.barcode.ilike(like),
            )
        )

    marketplace_filter = (request.args.get("marketplace") or "").strip().lower()
    status_filter = (request.args.get("status") or "").strip().lower()
    group_filter = (request.args.get("group") or "").strip().lower()
    listing_status_filter = (request.args.get("listing_status") or "").strip().lower()

    if marketplace_filter and marketplace_filter != "all":
        listing_query = listing_query.join(Store, MarketplaceListing.store_id == Store.id).filter(
            Store.platform.ilike(f"%{marketplace_filter}%")
        )

    if status_filter and status_filter != "all":
        listing_query = listing_query.filter(MarketplaceListing.status.ilike(f"%{status_filter}%"))

    if group_filter == "grouped":
        listing_query = listing_query.filter(MarketplaceListing.master_product_group_id.isnot(None))
    elif group_filter == "ungrouped":
        listing_query = listing_query.filter(MarketplaceListing.master_product_group_id.is_(None))

    if listing_status_filter == "linked":
        listing_query = listing_query.filter(MarketplaceListing.warehouse_stock_id.isnot(None))
    elif listing_status_filter == "unlinked":
        listing_query = listing_query.filter(MarketplaceListing.warehouse_stock_id.is_(None))

    # Apply view filters before count/pagination so searched FBA/FBM rows are not lost
    # after slicing the mixed marketplace result set.
    if view == "fba":
        listing_query = listing_query.filter(
            Store.platform.ilike("%amazon%"),
            ~MarketplaceListing.normalized_amazon_fulfillment_channel.in_(["MFN", "FBM", "MERCHANT"]),
        )
    elif view == "fbm":
        listing_query = listing_query.filter(
            Store.platform.ilike("%amazon%"),
            MarketplaceListing.normalized_amazon_fulfillment_channel.in_(["MFN", "FBM", "MERCHANT"]),
        )

    total_matching_rows = listing_query.count()
    total_pages = max(1, (total_matching_rows + row_limit - 1) // row_limit)
    page = min(page, total_pages)
    offset = (page - 1) * row_limit

    listing_rows = (
        listing_query
        .order_by(MarketplaceListing.updated_at.desc(), MarketplaceListing.id.desc())
        .offset(offset)
        .limit(row_limit)
        .all()
    )

    rows = []
    linked_stock_ids = set()

    for listing in listing_rows:
        stock = listing.warehouse_stock
        if stock:
            linked_stock_ids.add(stock.id)

        platform = (listing.store.platform if listing.store else "Marketplace") or "Marketplace"
        platform_lower = platform.lower()
        channel = (listing.normalized_amazon_fulfillment_channel or "").upper()
        is_amazon = "amazon" in platform_lower
        is_fbm = is_amazon and channel in ("MFN", "FBM", "MERCHANT")
        is_fba = is_amazon and not is_fbm
        location = f"{platform} {'FBA' if is_fba else 'FBM'}" if is_amazon else platform

        is_ebay_variation_child = bool(
            (not is_amazon)
            and (
                getattr(listing, "parent_item_id", None)
                or getattr(listing, "external_parent_id", None)
            )
            and str(getattr(listing, "external_listing_id", "") or "").strip()
        )

        display_sku = (
            listing.external_sku
            or (stock.sku if stock else None)
            or listing.external_listing_id
            or ""
        )

        fba_truth = None

        if is_fba:
            # Amazon FBA identity must be SKU-first.
            # ASIN is metadata only and must not merge multiple seller SKUs.
            # FNSKU is a secondary fallback only when seller SKU is unavailable.
            fba_truth = None

            if listing.external_sku:
                fba_truth = (
                    db.session.query(AmazonFBAInventory)
                    .filter(AmazonFBAInventory.seller_sku == listing.external_sku)
                    .first()
                )

            if not fba_truth and listing.fnsku and not listing.external_sku:
                fba_truth = (
                    db.session.query(AmazonFBAInventory)
                    .filter(AmazonFBAInventory.fnsku == listing.fnsku)
                    .first()
                )

        rows.append(SimpleNamespace(
            id=stock.id if stock else 0,
            inventory_item_id=None,
            item_id=None,
            marketplace_listing_id=listing.id,
            sku=display_sku,
            master_product_group_id=listing.master_product_group_id or (stock.master_product_group_id if stock else None),
            location=location,
            image_url=stock.image_url if stock else None,
            product_name=(stock.product_name if stock else None) or listing.title,
            title=listing.title,
            group_title=stock.group_title if stock else None,
            barcode=listing.fnsku or listing.barcode or (stock.barcode if stock else None),
            mcf_group_source=bool(is_fba),
            is_fba=bool(is_fba),
            is_fbm=bool(is_fbm),
            is_group_controlled=bool(stock.is_group_controlled) if stock else False,
            is_ebay_variation_child=is_ebay_variation_child,
            parent_item_id=getattr(listing, "parent_item_id", None),
            external_parent_id=getattr(listing, "external_parent_id", None),
            variation_sku_map=getattr(listing, "variation_sku_map", None),
            # Quantity authority:
            # AFN/FBA rows display imported marketplace quantity.
            # eBay variation child rows display their own imported marketplace quantity.
            # MFN/FBM rows display warehouse sellable quantity.
            available_quantity=(
                int(getattr(fba_truth, "available_quantity", 0) or 0)
                if is_fba
                else (
                    int(listing.last_marketplace_qty or 0)
                    if is_ebay_variation_child
                    else int(stock.sellable_quantity or 0)
                )
            ) if stock else (
                int(getattr(fba_truth, "available_quantity", 0) or 0)
                if is_fba
                else int(listing.last_marketplace_qty or 0)
            ),
            price=listing.price or 0,
            store_name=listing.store.name if listing.store else platform,
            platform=platform,
            external_listing_id=listing.external_listing_id,
            external_sku=listing.external_sku,
            asin=listing.asin,
            fnsku=listing.fnsku,
        ))

    if len(rows) < row_limit:
        stock_query = (
            db.session.query(WarehouseStock)
            .options(joinedload(WarehouseStock.warehouse))
            .filter(WarehouseStock.is_active == True)  # noqa: E712
            .filter(WarehouseStock.is_deleted == False)  # noqa: E712
        )

        if q:
            like = f"%{q}%"
            stock_query = stock_query.filter(
                or_(
                    WarehouseStock.sku.ilike(like),
                    WarehouseStock.product_name.ilike(like),
                    WarehouseStock.barcode.ilike(like),
                    WarehouseStock.group_title.ilike(like),
                )
            )

        unlinked_stock = (
            stock_query
            .order_by(WarehouseStock.updated_at.desc(), WarehouseStock.id.desc())
            .limit(row_limit)
            .all()
        )

        for stock in unlinked_stock:
            if stock.id in linked_stock_ids:
                continue

            rows.append(SimpleNamespace(
                id=stock.id,
                inventory_item_id=None,
                item_id=None,
                marketplace_listing_id=None,
                sku=stock.sku,
                master_product_group_id=stock.master_product_group_id,
                location="Warehouse",
                image_url=stock.image_url,
                product_name=stock.product_name,
                title=stock.product_name,
                group_title=stock.group_title,
                barcode=stock.barcode,
                mcf_group_source=False,
                is_fba=False,
                is_fbm=False,
                is_group_controlled=bool(stock.is_group_controlled),
                available_quantity=stock.sellable_quantity,
                price=0,
                store_name=stock.warehouse.name if stock.warehouse else "Warehouse",
                platform="Warehouse",
                external_listing_id=None,
                external_sku=None,
                asin=None,
                fnsku=None,
            ))

            if len(rows) >= row_limit:
                break

    if view == "available":
        rows = [row for row in rows if int(getattr(row, "available_quantity", 0) or 0) > 0]
    elif view == "low-stock":
        rows = [row for row in rows if int(getattr(row, "available_quantity", 0) or 0) <= 0]
    elif view == "listings":
        rows = [row for row in rows if getattr(row, "marketplace_listing_id", None)]
    elif view == "fba":
        rows = rows
    elif view == "fbm":
        rows = rows
    elif view == "groups":
        rows = [row for row in rows if getattr(row, "master_product_group_id", None) or getattr(row, "is_group_controlled", False)]

    # Real database totals for the top information boxes.
    # Do not calculate these from the limited visible rows.
    active_stock_rows = (
        db.session.query(WarehouseStock)
        .filter(WarehouseStock.is_active == True)  # noqa: E712
        .filter(WarehouseStock.is_deleted == False)  # noqa: E712
        .all()
    )

    total_skus = len(active_stock_rows)
    total_available = sum(int(getattr(stock, "sellable_quantity", 0) or 0) for stock in active_stock_rows)
    low_stock_count = sum(1 for stock in active_stock_rows if int(getattr(stock, "sellable_quantity", 0) or 0) <= 0)

    listing_count = (
        db.session.query(MarketplaceListing)
        .filter(MarketplaceListing.is_active == True)  # noqa: E712
        .count()
    )

    inventory_value = 0.0
    for row in rows:
        try:
            inventory_value += float(getattr(row, "price", 0) or 0) * int(getattr(row, "available_quantity", 0) or 0)
        except Exception:
            pass

    stats = SimpleNamespace(
        total_skus=total_skus,
        total_available=total_available,
        low_stock_count=low_stock_count,
        listing_count=listing_count,
        inventory_value=round(float(inventory_value), 2),
    )

    warehouse_items = SimpleNamespace(items=rows, total=total_matching_rows, visible=len(rows))
    pagination = SimpleNamespace(
        page=page,
        per_page=row_limit,
        total=total_matching_rows,
        total_pages=total_pages,
        has_prev=page > 1,
        has_next=page < total_pages,
        prev_page=max(1, page - 1),
        next_page=min(total_pages, page + 1),
    )

    html = render_template(
        "warehouse.html",
        warehouse_items=warehouse_items,
        stats=stats,
        search_query=q,
        active_view=view,
        per_page=row_limit,
        page=page,
        pagination=pagination,
        marketplace_filter=marketplace_filter,
        status_filter=status_filter,
        group_filter=group_filter,
        listing_status_filter=listing_status_filter,
    )
    return _patch_warehouse_phase1_ui(html, stats, q, view)


@governed_bp.post("/governed/actions/sku/dry-run")
def governed_sku_dry_run():
    from governed_execution import submit_governed_marketplace_action

    governed_payload = dict(request.get_json(silent=True) or {})
    governed_payload.setdefault("action", "push_inventory")

    result = submit_governed_marketplace_action(
        payload=governed_payload,
        actor=request.headers.get("X-Actor", "manual-governed-dry-run"),
        approval_type="manual_sku_dry_run_route",
        approval_id="manual_sku_dry_run",
        dry_run=True,
    )
    return jsonify(_governed_json_safe(result)), 200


@governed_bp.post("/governed/actions/listings/<int:listing_id>/push")
@login_required
def governed_listing_push(listing_id: int):
    """Listing push shortcut.

    Request body quantity is intentionally ignored.
    Governed push quantity must come from warehouse/listing truth only.
    """
    result = _push_one_listing(
        listing_id=listing_id,
        quantity=None,
        actor=_actor(),
        source="ui_listing_button",
    )
    return jsonify(_governed_json_safe(result)), 200


@governed_bp.post("/governed/actions/groups/<int:group_id>/push")
@login_required
def governed_group_push(group_id: int):
    """Group push shortcut.

    Product linking/grouping remains relationship-only.
    Quantity truth is resolved inside governed_push_execution.
    Request body quantity is intentionally ignored.
    """
    from services.governed_push_execution import push_group_listings

    result = push_group_listings(
        group_id=group_id,
        actor=_actor(),
        source="ui_group_button",
        actor_user=current_user if current_user and current_user.is_authenticated else None,
    )
    return jsonify(result), 200


@governed_bp.get("/governed/actions/history")
def governed_action_history():
    from extensions import db
    from models import SyncLog

    limit = min(int(request.args.get("limit", 50)), 200)
    query = db.session.query(SyncLog).filter(
        SyncLog.message.contains("governed_push")
    )
    listing_id = request.args.get("listing_id")
    if listing_id:
        query = query.filter(SyncLog.message.contains(f"listing_id={listing_id}"))
    rows = query.order_by(SyncLog.created_at.desc()).limit(limit).all()
    return jsonify({
        "success": True,
        "ok": True,
        "history": [
            {
                "id": row.id,
                "store_id": row.store_id,
                "status": row.status,
                "message": row.message,
                "items_synced": row.items_synced,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ],
    })


def _push_one_listing(*, listing_id: int, quantity, actor: str, source: str) -> dict:
    """Compatibility wrapper for existing governed callers.

    Quantity argument is ignored by design.
    Governed push quantity must come from warehouse/listing truth only.
    """
    from services.governed_push_execution import push_marketplace_listing

    return push_marketplace_listing(
        listing_id=listing_id,
        actor=actor,
        source=source,
        actor_user=current_user if current_user and current_user.is_authenticated else None,
    )


def _actor() -> str:
    try:
        if current_user and current_user.is_authenticated:
            return f"user:{current_user.id}"
    except Exception:
        pass
    return request.headers.get("X-Actor", "governed-ui-action")


def _blocked(reason: str, **extra) -> dict:
    result = {
        "success": False,
        "ok": False,
        "governed": True,
        "execution_blocked": True,
        "reason": reason,
    }
    result.update(extra)
    return result


def _patch_warehouse_phase1_ui(html: str, stats, search_query: str, active_view: str) -> str:
    """Safely wire existing warehouse.html without replacing the finished template.

    This keeps the approved layout intact and adds only runtime behavior:
    search, KPI filters, tab filters, and real dynamic KPI values.
    """
    listing_count = f"{int(getattr(stats, 'listing_count', 0) or 0):,}"
    inventory_value = f"£{float(getattr(stats, 'inventory_value', 0) or 0):,.0f}"
    html = html.replace("<strong>1,156</strong>", f"<strong>{listing_count}</strong>")
    html = html.replace("<strong>£183k</strong>", f"<strong>{inventory_value}</strong>")

    payload = {
        "searchQuery": search_query or "",
        "activeView": active_view or "all",
    }
    script = f"""
<script id="bt38Phase1WarehouseWiring">
(function() {{
  const state = {json.dumps(payload)};

  function go(view, extra) {{
    const url = new URL(window.location.href);
    if (view && view !== 'all') url.searchParams.set('view', view);
    else url.searchParams.delete('view');
    if (extra && Object.prototype.hasOwnProperty.call(extra, 'q')) {{
      const q = String(extra.q || '').trim();
      if (q) url.searchParams.set('q', q);
      else url.searchParams.delete('q');
    }}
    window.location.href = url.pathname + (url.search ? url.search : '');
  }}

  function wireSearch() {{
    const input = document.querySelector('.bt38-search-wrap input');
    if (!input) return;
    input.value = state.searchQuery || '';
    input.setAttribute('name', 'q');
    input.setAttribute('aria-label', 'Search SKU, product, ASIN, FNSKU or barcode');
    input.addEventListener('keydown', function(e) {{
      if (e.key === 'Enter') go(state.activeView || 'all', {{ q: input.value }});
    }});
  }}

  function makeClickable(el, title, fn) {{
    if (!el) return;
    el.setAttribute('role', 'button');
    el.setAttribute('tabindex', '0');
    el.style.cursor = 'pointer';
    if (title) el.setAttribute('title', title);
    el.addEventListener('click', fn);
    el.addEventListener('keydown', function(e) {{
      if (e.key === 'Enter' || e.key === ' ') {{ e.preventDefault(); fn(); }}
    }});
  }}

  function wireKpis() {{
    const cards = Array.from(document.querySelectorAll('.bt38-kpi-card'));
    makeClickable(cards[0], 'Show all SKUs', () => go('all'));
    makeClickable(cards[1], 'Show available stock', () => go('available'));
    makeClickable(cards[2], 'Show low stock', () => go('low-stock'));
    makeClickable(cards[3], 'Open system activity', () => {{ window.location.href = '/admin/system-activity'; }});
    makeClickable(cards[4], 'Show marketplace listings', () => go('listings'));
    makeClickable(cards[5], 'Show available stock value', () => go('available'));
  }}

  function wireTabs() {{
    const buttons = Array.from(document.querySelectorAll('.bt38-operational-tabs button'));
    const map = {{
      'Master Stock': () => go('all'),
      'FBA Read Only': () => go('fba'),
      'Group View': () => go('groups'),
      'Listings': () => go('listings'),
      'Orders': () => alert('Orders view will be wired after Master Stock runtime is proven.'),
      'Stock Transfer': () => alert('Stock Transfer will be wired after quantity and grouping are proven.')
    }};
    buttons.forEach(btn => {{
      const text = (btn.textContent || '').trim();
      if (map[text]) makeClickable(btn, text, map[text]);
      btn.classList.remove('active');
      if ((state.activeView === 'all' && text === 'Master Stock') ||
          (state.activeView === 'fba' && text === 'FBA Read Only') ||
          (state.activeView === 'groups' && text === 'Group View') ||
          (state.activeView === 'listings' && text === 'Listings')) {{
        btn.classList.add('active');
      }}
    }});
  }}

  function applyRuntimeState(row, state) {{
    if (!row || !state) return;

    row.dataset.runtimeRole = state.runtime_role || '';
    row.dataset.actionState = state.action_state || '';
    row.dataset.quantityAuthority = state.quantity_authority || '';

    const statusCell = row.querySelector('.bt38-status');
    if (statusCell) {{
      if (state.is_fba) {{
        statusCell.textContent = 'FBA Locked';
        statusCell.title = state.reason || 'FBA read-only';
      }} else if (state.is_pushable) {{
        statusCell.textContent = 'Governed Pushable';
        statusCell.title = state.reason || 'Governed pushable';
      }} else if (state.action_state === 'blocked') {{
        statusCell.textContent = 'Blocked';
        statusCell.title = state.reason || 'Blocked';
      }}
    }}

    const groupCell = row.querySelector('.bt38-group-pill');
    if (groupCell) {{
      if (state.is_mcf_eligible) {{
        groupCell.textContent = 'MCF';
        groupCell.title = 'MCF eligible: Amazon FBA only';
      }} else if (state.is_group_controlled) {{
        groupCell.title = 'Controlled by MasterProductGroup';
      }}
    }}

    const btn = row.querySelector('.bt38-marketplace-control');
    if (btn) {{
      if (state.is_fba) {{
        btn.title = 'FBA read-only';
        btn.setAttribute('aria-disabled', 'true');
      }} else if (state.is_pushable) {{
        btn.title = 'Governed pushable receiver';
      }} else {{
        btn.title = state.reason || 'Not pushable';
      }}
    }}
  }}


  function attentionReason(state) {{
    if (!state) return '';
    return state.reason || state.last_push_error || state.push_state || state.action_state || 'Needs review';
  }}

  function isAttentionState(state) {{
    if (!state) return false;

    const action = String(state.action_state || '').toLowerCase();
    const push = String(state.push_state || '').toLowerCase();
    const reason = String(attentionReason(state) || '').toLowerCase();

    return (
      action === 'blocked' ||
      push === 'blocked' ||
      push === 'needs_review' ||
      reason.includes('failed') ||
      reason.includes('review') ||
      reason.includes('fba') ||
      reason.includes('read-only')
    );
  }}

  function injectWarehouseAttentionBox(rows) {{
    if (!Array.isArray(rows)) return;
    if (document.querySelector('.bt38-warehouse-attention-box')) return;

    const attention = rows.filter(isAttentionState).slice(0, 8);

    if (!attention.length) return;

    const wrapper = document.createElement('div');
    wrapper.className = 'bt38-warehouse-attention-box';

    wrapper.innerHTML =
      '<div class="bt38-attention-head">' +
        '<strong>SKUs needing action</strong>' +
        '<span>Real warehouse/listing issues only</span>' +
      '</div>' +
      '<div class="bt38-attention-list"></div>';

    const list = wrapper.querySelector('.bt38-attention-list');

    attention.forEach(state => {{
      const sku = state.sku || state.external_sku || 'Unknown SKU';

      const card = document.createElement('a');
      card.className = 'bt38-attention-item';
      card.href = '/warehouse?q=' + encodeURIComponent(sku) + '&view=all&per_page=100';

      card.innerHTML =
        '<strong>' + sku + '</strong>' +
        '<span>Needs action</span>' +
        '<small>' + String(attentionReason(state)).slice(0, 140) + '</small>';

      list.appendChild(card);
    }});

    const kpi = document.querySelector('.bt38-kpi-row');

    if (kpi && kpi.parentNode) {{
      kpi.parentNode.insertBefore(wrapper, kpi);
    }}
  }}

  function loadRuntimeOverlay() {{
    fetch('/governed/warehouse/runtime-state')
      .then(r => r.json())
      .then(data => {{
        if (!data || !Array.isArray(data.rows)) return;

        injectWarehouseAttentionBox(data.rows);

        const byListing = new Map();
        const byStock = new Map();

        data.rows.forEach(state => {{
          if (state.listing_id) byListing.set(String(state.listing_id), state);
          if (state.warehouse_stock_id) byStock.set(String(state.warehouse_stock_id), state);
        }});

        document.querySelectorAll('tr[data-stock-id], tr[data-listing-id]').forEach(row => {{
          const listingId = row.dataset.listingId || '';
          const stockId = row.dataset.stockId || '';

          const state =
            (listingId && byListing.get(String(listingId))) ||
            (stockId && byStock.get(String(stockId)));

          applyRuntimeState(row, state);
        }});
      }})
      .catch(() => console.warn('Governed runtime overlay unavailable'));
  }}

  document.addEventListener('DOMContentLoaded', function() {{
    wireSearch();
    wireKpis();
    wireTabs();
    loadRuntimeOverlay();
  }});
}})();
</script>
<style id="bt38Phase1WarehouseWiringStyle">
.bt38-kpi-card:hover{{box-shadow:0 0 0 2px rgba(37,99,235,.10);}}
tr[data-runtime-role="quantity_authority"]{{outline:1px solid rgba(37,99,235,.12);}}
tr[data-action-state="governed_pushable"]{{outline:1px solid rgba(22,163,74,.12);}}
tr[data-action-state="skip_before_push"]{{outline:1px solid rgba(245,158,11,.16);}}
tr[data-action-state="blocked"]{{outline:1px solid rgba(220,38,38,.14);}}
.bt38-warehouse-attention-box{{margin:14px 0;padding:12px;border:1px solid rgba(15,23,42,.12);border-radius:12px;background:#fff;}}
.bt38-attention-head{{display:flex;justify-content:space-between;gap:10px;margin-bottom:10px;}}
.bt38-attention-head span{{color:#64748b;font-size:12px;}}
.bt38-attention-list{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:8px;}}
.bt38-attention-item{{display:block;padding:10px;border:1px solid rgba(15,23,42,.10);border-radius:10px;text-decoration:none;color:inherit;background:#fafafa;}}
.bt38-attention-item strong,.bt38-attention-item span,.bt38-attention-item small{{display:block;}}
.bt38-attention-item span{{font-size:13px;margin-top:2px;}}
.bt38-attention-item small{{color:#64748b;margin-top:3px;}}
</style>
"""
    return html + script


@governed_bp.post("/amazon-inventory-hydration/manual-run")
def governed_amazon_inventory_hydration_manual_run():
    from services.governed_amazon_inventory_hydration import hydrate_amazon_inventory

    result = hydrate_amazon_inventory()

    return jsonify({
        "success": True,
        "manual": True,
        "governed": True,
        "auto_execution": False,
        "result": result,
    })



@governed_bp.get("/governed/product-linking/data")
def governed_product_linking_data_compat():
    """Governed compatibility endpoint for old product-linking data calls.

    This keeps the frontend inside the governed namespace.
    It is read-only and does not push, sync, repair, or mutate marketplace state.
    """
    from extensions import db
    from models import WarehouseStock, MarketplaceListing, AmazonFBAInventory
    from sqlalchemy import or_

    search = (request.args.get("search") or request.args.get("q") or "").strip()
    limit_raw = request.args.get("limit") or request.args.get("per_page") or 25
    page_raw = request.args.get("page") or 1

    try:
        per_page = int(limit_raw)
    except Exception:
        per_page = 50

    try:
        page = int(page_raw)
    except Exception:
        page = 1

    per_page = max(1, min(per_page, 100))
    page = max(1, page)
    offset = (page - 1) * per_page

    stock_query = db.session.query(WarehouseStock).filter(
        WarehouseStock.is_active == True,  # noqa: E712
        WarehouseStock.is_deleted == False,  # noqa: E712
    )

    listing_query = db.session.query(MarketplaceListing).filter(
        MarketplaceListing.is_active == True  # noqa: E712
    )

    if search:
        like = f"%{search}%"

        matching_listing_stock_ids = [
            row[0]
            for row in (
                db.session.query(MarketplaceListing.warehouse_stock_id)
                .filter(MarketplaceListing.is_active == True)  # noqa: E712
                .filter(MarketplaceListing.warehouse_stock_id.isnot(None))
                .filter(or_(
                    MarketplaceListing.external_sku.ilike(like),
                    MarketplaceListing.title.ilike(like),
                    MarketplaceListing.external_listing_id.ilike(like),
                    MarketplaceListing.asin.ilike(like),
                    MarketplaceListing.fnsku.ilike(like),
                    MarketplaceListing.barcode.ilike(like),
                    MarketplaceListing.parent_item_id.ilike(like),
                    MarketplaceListing.external_parent_id.ilike(like),
                    MarketplaceListing.variation_sku_map.ilike(like),
                ))
                .distinct()
                .all()
            )
            if row[0] is not None
        ]

        stock_search_clauses = [
            WarehouseStock.sku.ilike(like),
            WarehouseStock.product_name.ilike(like),
            WarehouseStock.barcode.ilike(like),
            WarehouseStock.group_title.ilike(like),
        ]

        if matching_listing_stock_ids:
            stock_search_clauses.append(WarehouseStock.id.in_(matching_listing_stock_ids))

        stock_query = stock_query.filter(or_(*stock_search_clauses))

        listing_query = listing_query.filter(or_(
            MarketplaceListing.external_sku.ilike(like),
            MarketplaceListing.title.ilike(like),
            MarketplaceListing.external_listing_id.ilike(like),
            MarketplaceListing.asin.ilike(like),
            MarketplaceListing.fnsku.ilike(like),
            MarketplaceListing.barcode.ilike(like),
            MarketplaceListing.parent_item_id.ilike(like),
            MarketplaceListing.external_parent_id.ilike(like),
            MarketplaceListing.variation_sku_map.ilike(like),
        ))

    total_stock = stock_query.count()
    total_listings = listing_query.count()
    total_pages_stock = max(1, (total_stock + per_page - 1) // per_page)

    if page > total_pages_stock:
        page = total_pages_stock
        offset = (page - 1) * per_page

    stock_rows = stock_query.order_by(WarehouseStock.id.desc()).offset(offset).limit(per_page).all()

    # Product Linking groups must be shown as full relationships.
    # If search finds one warehouse row in a group, include every active warehouse row
    # in that same group before building listing_rows.
    # This keeps FBA-led groups from appearing half-linked in the UI.
    matched_group_ids = {
        int(getattr(stock, "master_product_group_id"))
        for stock in stock_rows
        if getattr(stock, "master_product_group_id", None)
    }

    if matched_group_ids:
        group_stock_rows = (
            db.session.query(WarehouseStock)
            .filter(WarehouseStock.is_active == True)  # noqa: E712
            .filter(WarehouseStock.is_deleted == False)  # noqa: E712
            .filter(WarehouseStock.master_product_group_id.in_(list(matched_group_ids)))
            .all()
        )

        stock_by_id = {int(stock.id): stock for stock in stock_rows}
        for stock in group_stock_rows:
            stock_by_id[int(stock.id)] = stock
        stock_rows = list(stock_by_id.values())

        total_stock = max(total_stock, len(stock_rows))
        total_pages_stock = max(1, (total_stock + per_page - 1) // per_page)

    stock_ids_on_page = [stock.id for stock in stock_rows]
    if stock_ids_on_page:
        listing_rows = (
            db.session.query(MarketplaceListing)
            .filter(MarketplaceListing.is_active == True)  # noqa: E712
            .filter(MarketplaceListing.warehouse_stock_id.in_(stock_ids_on_page))
            .order_by(MarketplaceListing.id.desc())
            .all()
        )
    else:
        listing_rows = []

    fba_qty_by_sku = {}
    fba_qty_by_fnsku = {}

    fba_skus = {
        str(getattr(listing, "external_sku", "") or "").strip()
        for listing in listing_rows
        if bool(getattr(listing, "is_fba", False))
        and str(getattr(listing, "external_sku", "") or "").strip()
    }

    fba_fnskus = {
        str(getattr(listing, "fnsku", "") or "").strip()
        for listing in listing_rows
        if bool(getattr(listing, "is_fba", False))
        and str(getattr(listing, "fnsku", "") or "").strip()
    }

    if fba_skus or fba_fnskus:
        fba_rows = (
            db.session.query(AmazonFBAInventory)
            .filter(
                or_(
                    AmazonFBAInventory.seller_sku.in_(list(fba_skus) or ["__BT38_NO_SKU__"]),
                    AmazonFBAInventory.fnsku.in_(list(fba_fnskus) or ["__BT38_NO_FNSKU__"]),
                )
            )
            .all()
        )

        for row in fba_rows:
            qty = int(getattr(row, "available_quantity", 0) or 0)
            if getattr(row, "seller_sku", None):
                fba_qty_by_sku[str(row.seller_sku).strip()] = qty
            if getattr(row, "fnsku", None):
                fba_qty_by_fnsku[str(row.fnsku).strip()] = qty

    listings_by_stock = {}
    unlinked_listings = []

    for listing in listing_rows:
        listing_is_fba = bool(getattr(listing, "is_fba", False))
        listing_sku = str(getattr(listing, "external_sku", "") or "").strip()
        listing_fnsku = str(getattr(listing, "fnsku", "") or "").strip()

        fba_available_quantity = None
        if listing_is_fba:
            fba_available_quantity = fba_qty_by_sku.get(listing_sku)
            if fba_available_quantity is None:
                fba_available_quantity = fba_qty_by_fnsku.get(listing_fnsku)

        listing_platform = listing.store.platform if listing.store else getattr(listing, "platform", "")
        listing_channel = str(getattr(listing, "normalized_amazon_fulfillment_channel", None) or listing.amazon_fulfillment_channel or "").upper()
        listing_is_amazon = "amazon" in str(listing_platform).lower()
        listing_is_ebay = "ebay" in str(listing_platform).lower()
        listing_is_fbm = listing_is_amazon and listing_channel in ("MFN", "FBM", "MERCHANT")

        if listing_is_fba:
            push_status = "read_only"
            push_status_label = "FBA read-only"
            push_status_reason = "Amazon controls FBA/AFN stock. Group push skips this listing."
            listing_pushable = False
        elif listing_is_fbm or listing_is_ebay:
            push_status = "pushable"
            push_status_label = "Pushable"
            push_status_reason = "Seller-controlled marketplace stock can be updated from warehouse truth."
            listing_pushable = True
        else:
            push_status = "not_pushable"
            push_status_label = "Not pushable"
            push_status_reason = "Listing is not eligible for governed marketplace push."
            listing_pushable = False

        listing_payload = {
            "id": listing.id,
            "external_sku": listing.external_sku,
            "sku": listing.external_sku,
            "title": listing.title,
            "external_listing_id": listing.external_listing_id,
            "external_id": listing.external_listing_id,
            "asin": listing.asin,
            "fnsku": listing.fnsku,
            "warehouse_stock_id": listing.warehouse_stock_id,
            "master_product_group_id": listing.master_product_group_id,
            "store_id": listing.store_id,
            "store_name": listing.store.name if listing.store else "",
            "platform": listing_platform,
            "amazon_fulfillment_channel": listing.amazon_fulfillment_channel,
            "is_fba": listing_is_fba,
            "is_pushable": listing_pushable,
            "push_status": push_status,
            "push_status_label": push_status_label,
            "push_status_reason": push_status_reason,
            "effective_quantity": getattr(listing, "effective_quantity", 0),
            "fba_available_quantity": fba_available_quantity,
        }

        if listing.warehouse_stock_id:
            listings_by_stock.setdefault(listing.warehouse_stock_id, []).append(listing_payload)
        else:
            unlinked_listings.append(listing_payload)

    # Product Linking display is one row per relationship group, not one row per
    # warehouse stock. Multiple warehouse rows that share the same
    # master_product_group_id must render as one Product Group with all linked listings.
    grouped_stock_rows = {}
    ungrouped_stock_rows = []

    for stock in stock_rows:
        stock_group_id = getattr(stock, "master_product_group_id", None)
        if stock_group_id:
            grouped_stock_rows.setdefault(int(stock_group_id), []).append(stock)
        else:
            ungrouped_stock_rows.append(stock)

    display_stock_rows = []

    # When search matches a marketplace listing that already belongs to a governed group,
    # do not let an old ungrouped warehouse SKU shortcut row override the real relationship row.
    # Example: Amazon SKU search may match an orphan warehouse row, while the live listing
    # is actually attached to the grouped eBay/Amazon authority stock row.
    if search and matching_listing_stock_ids and grouped_stock_rows:
        grouped_stock_ids = {
            int(stock.id)
            for group_stocks in grouped_stock_rows.values()
            for stock in group_stocks
            if getattr(stock, "id", None) is not None
        }

        grouped_listing_skus = {
            str(item.get("sku") or item.get("external_sku") or "").strip().lower()
            for stock_id in grouped_stock_ids
            for item in listings_by_stock.get(stock_id, [])
            if str(item.get("sku") or item.get("external_sku") or "").strip()
        }

        def is_stale_orphan_stock(stock):
            stock_sku = str(getattr(stock, "sku", "") or "").strip().lower()
            if not stock_sku:
                return False
            if stock_sku not in grouped_listing_skus:
                return False
            if getattr(stock, "master_product_group_id", None):
                return False
            if listings_by_stock.get(stock.id):
                return False
            return True

        ungrouped_stock_rows = [
            stock for stock in ungrouped_stock_rows
            if not is_stale_orphan_stock(stock)
        ]

    for group_id, group_stocks in grouped_stock_rows.items():
        def stock_has_fba_listing(stock):
            return any(
                bool(item.get("is_fba")) or str(item.get("amazon_fulfillment_channel") or "").upper() in ("AFN", "FBA")
                for item in listings_by_stock.get(stock.id, [])
            )

        def stock_linked_count(stock):
            return len(listings_by_stock.get(stock.id, []))

        # Prefer FBA authority row for FBA-led groups, otherwise prefer the row with listings.
        authority_stock = sorted(
            group_stocks,
            key=lambda stock: (
                0 if stock_has_fba_listing(stock) else 1,
                -stock_linked_count(stock),
                int(stock.id),
            )
        )[0]
        display_stock_rows.append(authority_stock)

        merged_linked = []
        seen_listing_ids = set()
        for group_stock in group_stocks:
            for item in listings_by_stock.get(group_stock.id, []):
                item_id = int(item.get("id")) if item.get("id") is not None else None
                if item_id is not None and item_id in seen_listing_ids:
                    continue
                if item_id is not None:
                    seen_listing_ids.add(item_id)
                merged_linked.append(item)

        listings_by_stock[authority_stock.id] = merged_linked

    display_stock_rows.extend(ungrouped_stock_rows)

    warehouse_products = []
    for stock in display_stock_rows:
        linked = listings_by_stock.get(stock.id, [])

        platforms = sorted({str(item.get("platform") or "").strip() for item in linked if item.get("platform")})

        is_fba_group = any(
            bool(item.get("is_fba"))
            or str(item.get("amazon_fulfillment_channel") or "").upper() in ("AFN", "FBA")
            for item in linked
        )

        fba_authority_quantity = None
        if is_fba_group:
            for item in linked:
                if item.get("fba_available_quantity") is not None:
                    fba_authority_quantity = item.get("fba_available_quantity")
                    break

        display_quantity = (
            fba_authority_quantity
            if fba_authority_quantity is not None
            else getattr(stock, "sellable_quantity", 0)
        )

        warehouse_products.append({
            "id": stock.id,
            "sku": stock.sku,
            "name": stock.product_name,
            "product_name": stock.product_name,
            "group_name": stock.group_title or stock.product_name or stock.sku,
            "barcode": stock.barcode,
            "group_title": stock.group_title,
            "master_product_group_id": stock.master_product_group_id,
            "is_group_controlled": bool(getattr(stock, "is_group_controlled", False)),
            "is_fba_group": is_fba_group,
            "fba_authority_quantity": fba_authority_quantity,
            "quantity": display_quantity,
            "available_quantity": display_quantity,
            "sellable_quantity": display_quantity,
            "linked_count": len(linked),
            "platforms": platforms,
            "listings": linked,
        })

    unlinked_by_platform = {}
    for item in unlinked_listings:
        platform = item.get("platform") or "Unknown"
        unlinked_by_platform.setdefault(platform, []).append(item)

    return jsonify({
        "success": True,
        "ok": True,
        "governed": True,
        "read_only": True,
        "truth_source": "WarehouseStock",
        "mode": "grouping_index",
        "search_term": search,
        "page": page,
        "per_page": per_page,
        "total_stock": total_stock,
        "total_listings": total_listings,
        "total_pages": total_pages_stock,
        "has_prev": page > 1,
        "has_next": page < total_pages_stock,
        "prev_page": max(1, page - 1),
        "next_page": min(total_pages_stock, page + 1),
        "warehouse_products": warehouse_products,
        "unlinked_listings": unlinked_listings,
        "unlinked_by_platform": unlinked_by_platform,
        "all_marketplace_listings": [
            item
            for grouped in listings_by_stock.values()
            for item in grouped
        ] + unlinked_listings,
        "all_stores": [],
        "warehouse": warehouse_products,
        "listings": [
            item
            for grouped in listings_by_stock.values()
            for item in grouped
        ] + unlinked_listings,
    })


@governed_bp.get("/governed/product-linking/search-all-listings")
def governed_product_linking_search_all_listings_compat():
    """Lightweight marketplace listing search for Product Linking modal.

    Read-only. No push, sync, import, repair, or mutation.
    """
    from extensions import db
    from models import MarketplaceListing
    from sqlalchemy import or_

    search = (request.args.get("search") or request.args.get("q") or "").strip()
    exclude_warehouse_raw = request.args.get("exclude_warehouse")
    limit_raw = request.args.get("limit") or 20

    try:
        limit = int(limit_raw)
    except Exception:
        limit = 20
    limit = max(1, min(limit, 20))

    exclude_warehouse_id = None
    if exclude_warehouse_raw not in (None, "", "0", "null", "None"):
        try:
            exclude_warehouse_id = int(exclude_warehouse_raw)
        except Exception:
            exclude_warehouse_id = None

    query = db.session.query(MarketplaceListing).filter(
        MarketplaceListing.is_active == True  # noqa: E712
    )

    if exclude_warehouse_id is not None:
        query = query.filter(or_(
            MarketplaceListing.warehouse_stock_id.is_(None),
            MarketplaceListing.warehouse_stock_id != exclude_warehouse_id,
        ))

    if search:
        like = f"%{search}%"
        query = query.filter(or_(
            MarketplaceListing.external_sku.ilike(like),
            MarketplaceListing.title.ilike(like),
            MarketplaceListing.external_listing_id.ilike(like),
            MarketplaceListing.asin.ilike(like),
            MarketplaceListing.fnsku.ilike(like),
            MarketplaceListing.barcode.ilike(like),
            MarketplaceListing.parent_item_id.ilike(like),
            MarketplaceListing.external_parent_id.ilike(like),
            MarketplaceListing.variation_sku_map.ilike(like),
        ))
    else:
        query = query.filter(MarketplaceListing.warehouse_stock_id.is_(None))

    rows = query.order_by(MarketplaceListing.id.desc()).limit(limit).all()

    listings = []
    for listing in rows:
        store = getattr(listing, "store", None)
        listing_platform = store.platform if store else getattr(listing, "platform", "")
        listing_channel = str(getattr(listing, "normalized_amazon_fulfillment_channel", None) or listing.amazon_fulfillment_channel or "").upper()
        listing_is_fba = bool(getattr(listing, "is_fba", False))
        listing_is_amazon = "amazon" in str(listing_platform).lower()
        listing_is_ebay = "ebay" in str(listing_platform).lower()
        listing_is_fbm = listing_is_amazon and listing_channel in ("MFN", "FBM", "MERCHANT")

        if listing_is_fba:
            push_status = "read_only"
            push_status_label = "FBA read-only"
            push_status_reason = "Amazon controls FBA/AFN stock. Group push skips this listing."
            listing_pushable = False
        elif listing_is_fbm or listing_is_ebay:
            push_status = "pushable"
            push_status_label = "Pushable"
            push_status_reason = "Seller-controlled marketplace stock can be updated from warehouse truth."
            listing_pushable = True
        else:
            push_status = "not_pushable"
            push_status_label = "Not pushable"
            push_status_reason = "Listing is not eligible for governed marketplace push."
            listing_pushable = False

        listings.append({
            "id": listing.id,
            "external_sku": listing.external_sku,
            "sku": listing.external_sku,
            "title": listing.title,
            "external_listing_id": listing.external_listing_id,
            "external_id": listing.external_listing_id,
            "asin": listing.asin,
            "fnsku": listing.fnsku,
            "warehouse_stock_id": listing.warehouse_stock_id,
            "master_product_group_id": listing.master_product_group_id,
            "store_id": listing.store_id,
            "store_name": store.name if store else "",
            "platform": listing_platform,
            "amazon_fulfillment_channel": listing.amazon_fulfillment_channel,
            "fulfillment": listing.amazon_fulfillment_channel,
            "is_fba": listing_is_fba,
            "is_pushable": listing_pushable,
            "push_status": push_status,
            "push_status_label": push_status_label,
            "push_status_reason": push_status_reason,
        })

    return jsonify({
        "success": True,
        "ok": True,
        "governed": True,
        "read_only": True,
        "mode": "listing_search",
        "search_term": search,
        "limit": limit,
        "count": len(listings),
        "listings": listings,
    }), 200


@governed_bp.get("/governed/product-linking/search-warehouse")
def governed_product_linking_search_warehouse_compat():
    data_response = governed_product_linking_data_compat()
    return data_response


@governed_bp.get("/governed/product-linking/diagnostics/<int:warehouse_id>")
def governed_product_linking_diagnostics_compat(warehouse_id: int):
    """Safe governed diagnostic shell.

    Read-only. No repair, no sync, no push.
    """
    from extensions import db
    from models import WarehouseStock, MarketplaceListing

    stock = db.session.get(WarehouseStock, warehouse_id)
    linked = (
        db.session.query(MarketplaceListing)
        .filter(MarketplaceListing.warehouse_stock_id == warehouse_id)
        .limit(100)
        .all()
    )

    return jsonify({
        "success": True,
        "ok": True,
        "governed": True,
        "read_only": True,
        "warehouse_id": warehouse_id,
        "stock_found": bool(stock),
        "linked_listing_count": len(linked),
        "linked_listings": [
            {
                "id": l.id,
                "external_sku": l.external_sku,
                "title": l.title,
                "store_id": l.store_id,
                "master_product_group_id": l.master_product_group_id,
            }
            for l in linked
        ],
        "message": "Governed diagnostics are read-only. Repair actions are blocked until explicitly governed.",
    })


@governed_bp.post("/governed/product-linking/repair/<int:warehouse_id>")
def governed_product_linking_repair_compat(warehouse_id: int):
    """Safe governed repair shell.

    This intentionally blocks repair mutation until the repair rules are governed.
    """
    return jsonify({
        "success": False,
        "ok": False,
        "governed": True,
        "repair_blocked": True,
        "warehouse_id": warehouse_id,
        "message": "Governed repair is not enabled yet. This old repair action is safely blocked.",
    }), 409


@governed_bp.post("/governed/product-linking/bulk-action")
def governed_product_linking_bulk_action_compat():
    """Safe governed bulk shell.

    Bulk mutation is blocked until the governed bulk model is proven.
    """
    return jsonify({
        "success": False,
        "ok": False,
        "governed": True,
        "bulk_blocked": True,
        "message": "Governed bulk product-linking action is not enabled yet.",
    }), 409


@governed_bp.post("/governed/warehouse/<int:stock_id>/upload-image")
def governed_warehouse_upload_image_compat(stock_id: int):
    """Safe governed upload shell.

    Blocks upload mutation until governed storage policy is approved.
    """
    return jsonify({
        "success": False,
        "ok": False,
        "governed": True,
        "upload_blocked": True,
        "stock_id": stock_id,
        "message": "Governed warehouse image upload is not enabled yet.",
    }), 409




@governed_bp.post("/governed/actions/listings/<int:listing_id>/quantity")
def governed_listing_quantity_update(listing_id: int):
    """Governed local warehouse quantity update.

    This updates warehouse truth only. It does not push to marketplaces.
    Marketplace push remains a separate explicit governed action.
    """
    from extensions import db
    from models import MarketplaceListing, WarehouseStock
    from flask import request, jsonify

    listing = db.session.get(MarketplaceListing, listing_id)
    if not listing:
        return jsonify(success=False, ok=False, error="listing_not_found"), 404

    stock = listing.warehouse_stock
    if not stock and listing.warehouse_stock_id:
        stock = db.session.get(WarehouseStock, listing.warehouse_stock_id)

    if not stock:
        return jsonify(success=False, ok=False, error="warehouse_stock_not_found"), 404

    payload = request.get_json(silent=True) or {}
    try:
        qty = int(payload.get("quantity"))
    except Exception:
        return jsonify(success=False, ok=False, error="invalid_quantity"), 400

    if qty < 0:
        return jsonify(success=False, ok=False, error="negative_quantity_not_allowed"), 400

    writable_columns = [
        "quantity",
        "available_quantity",
        "stock_quantity",
        "on_hand_quantity",
        "qty",
    ]

    updated_column = None
    stock_columns = set(stock.__table__.columns.keys())
    for col in writable_columns:
        if col in stock_columns:
            setattr(stock, col, qty)
            updated_column = col
            break

    if not updated_column:
        return jsonify(
            success=False,
            ok=False,
            governed=True,
            error="no_supported_quantity_column",
            message="No writable warehouse quantity column was found.",
        ), 409

    # eBay variation rows render from listing-layer marketplace quantity fields.
    # Keep variation child rows aligned with the same saved quantity as normal listings.
    platform = (listing.store.platform if listing.store else "") or ""
    is_ebay_variation = (
        "ebay" in platform.lower()
        and (
            getattr(listing, "parent_item_id", None)
            or getattr(listing, "external_parent_id", None)
            or getattr(listing, "variation_sku_map", None)
        )
    )

    if is_ebay_variation:
        listing.last_marketplace_qty = qty
        listing.last_push_quantity = qty
        listing.last_push_status = "pending"
        listing.push_state = "active"
        listing.updated_at = datetime.utcnow()

    db.session.commit()

    return jsonify(
        success=True,
        ok=True,
        governed=True,
        listing_id=listing.id,
        warehouse_stock_id=stock.id,
        quantity=qty,
        updated_column=updated_column,
        listing_quantity_updated=bool(is_ebay_variation),
        message="Warehouse quantity saved locally. Use Push to sync marketplace.",
    )


@governed_bp.post("/governed/actions/listings/<int:listing_id>/price")
def governed_listing_price_update(listing_id: int):
    """Governed local listing price update.

    This updates local listing price only. It does not push to marketplaces.
    """
    from extensions import db
    from models import MarketplaceListing
    from flask import request, jsonify

    listing = db.session.get(MarketplaceListing, listing_id)
    if not listing:
        return jsonify(success=False, ok=False, error="listing_not_found"), 404

    payload = request.get_json(silent=True) or {}
    try:
        price = float(payload.get("price"))
    except Exception:
        return jsonify(success=False, ok=False, error="invalid_price"), 400

    if price < 0:
        return jsonify(success=False, ok=False, error="negative_price_not_allowed"), 400

    if "price" not in set(listing.__table__.columns.keys()):
        return jsonify(success=False, ok=False, error="price_column_missing"), 409

    listing.price = price
    db.session.commit()

    return jsonify(
        success=True,
        ok=True,
        governed=True,
        listing_id=listing.id,
        price=price,
        message="Listing price saved locally. Use Push to sync marketplace.",
    )



@governed_bp.post("/governed/stores/<int:store_id>/sync")
def governed_store_sync_shortcut(store_id):
    """Single governed store sync shortcut.

    Page buttons are shortcuts only.
    SystemConfig fuse box is the authority.
    Store identity makes the sync store-aware.
    """
    from datetime import datetime
    from flask import jsonify, request
    from extensions import db
    from models import Store, SystemLog
    from services.runtime_action_guard import is_runtime_action_allowed
    from services.governed_warehouse_sync import run_governed_warehouse_sync

    body = request.get_json(silent=True) or {}
    shortcut_source = (
        body.get("shortcut_source")
        or request.headers.get("X-BT38-Shortcut")
        or request.headers.get("X-Actor")
        or "store_sync_shortcut"
    )

    store = Store.query.get_or_404(store_id)

    guard = is_runtime_action_allowed(
        store=store,
        action_type="sync",
        manual=True,
        context={
            "source": shortcut_source,
            "shortcut": True,
            "authority": "SystemConfig fuse box",
        },
    )

    def log_shortcut(status, message):
        try:
            db.session.add(SystemLog(
                log_type="governed_shortcut_sync",
                message=message,
                details=(
                    f"store_id={store.id} platform={store.platform} "
                    f"shortcut=true source={shortcut_source} status={status} "
                    f"allowed={guard.get('allowed')} reason={guard.get('reason')}"
                )[:1000],
                created_at=datetime.utcnow(),
            ))
            db.session.commit()
        except Exception:
            db.session.rollback()

    if not guard.get("allowed"):
        log_shortcut("blocked", "Sync shortcut blocked by fuse box")
        return jsonify(
            ok=False,
            success=False,
            governed=True,
            shortcut=True,
            status="blocked",
            action_type="sync",
            store_id=store.id,
            platform=store.platform,
            fuse_box_checked=True,
            allowed=False,
            reason=guard.get("reason"),
            guard=guard,
        ), 200

    result = run_governed_warehouse_sync(
        store_id=store.id,
        actor=shortcut_source,
    )

    log_shortcut("completed", "Sync shortcut executed through fuse box")

    if isinstance(result, dict):
        result.update({
            "shortcut": True,
            "action_type": "sync",
            "store_id": store.id,
            "platform": store.platform,
            "fuse_box_checked": True,
            "allowed": True,
            "guard": guard,
        })
        return jsonify(_governed_json_safe(result)), 200

    return jsonify(
        ok=True,
        success=True,
        governed=True,
        shortcut=True,
        action_type="sync",
        store_id=store.id,
        platform=store.platform,
        result=result,
        fuse_box_checked=True,
        allowed=True,
        guard=guard,
    ), 200


@governed_bp.post("/governed/warehouse/sync")
def governed_warehouse_sync_manual_run():
    """One governed Sync All shortcut.

    One button sends a signal to stores/markets that are switched ON.
    This route is an orchestrator, not a bypass executor.

    Normal Sync All does not ask for a password.
    Password is only required when changing the sync/fuse structure.
    """
    from datetime import datetime
    from flask import jsonify, request
    from extensions import db
    from models import Store, SystemLog
    from services.runtime_action_guard import is_runtime_action_allowed
    from services.governed_warehouse_sync import run_governed_warehouse_sync

    body = dict(request.get_json(silent=True) or {})
    shortcut_source = (
        body.get("shortcut_source")
        or request.headers.get("X-BT38-Shortcut")
        or request.headers.get("X-Actor")
        or "warehouse_sync_all_shortcut"
    )

    stores = (
        Store.query
        .filter(Store.is_active == True)  # noqa: E712
        .filter(Store.store_mode == "live")
        .filter(Store.fbm_sync_enabled == True)  # noqa: E712
        .order_by(Store.id)
        .all()
    )

    summary = {
        "ok": True,
        "success": True,
        "governed": True,
        "shortcut": True,
        "action_type": "sync_all",
        "authority": "SystemConfig fuse box",
        "fuse_box_checked": True,
        "source": shortcut_source,
        "stores_total": len(stores),
        "stores_allowed": 0,
        "stores_blocked": 0,
        "stores_failed": 0,
        "results": [],
    }

    for store in stores:
        guard = is_runtime_action_allowed(
            store=store,
            action_type="sync",
            manual=True,
            context={
                "source": shortcut_source,
                "shortcut": True,
                "scope": "sync_all_switched_on_markets",
                "authority": "SystemConfig fuse box",
            },
        )

        if not guard.get("allowed"):
            summary["stores_blocked"] += 1
            summary["results"].append({
                "store_id": store.id,
                "store": store.name,
                "platform": store.platform,
                "status": "blocked",
                "allowed": False,
                "reason": guard.get("reason"),
                "guard": guard,
            })
            continue

        try:
            result = run_governed_warehouse_sync(
                store_id=store.id,
                actor=shortcut_source,
            )
            summary["stores_allowed"] += 1
            summary["results"].append({
                "store_id": store.id,
                "store": store.name,
                "platform": store.platform,
                "status": "completed",
                "allowed": True,
                "guard": guard,
                "result": result,
            })
        except Exception as exc:
            summary["stores_failed"] += 1
            summary["success"] = False
            summary["ok"] = False
            summary["results"].append({
                "store_id": store.id,
                "store": store.name,
                "platform": store.platform,
                "status": "failed",
                "allowed": True,
                "guard": guard,
                "reason": str(exc),
            })

    try:
        db.session.add(SystemLog(
            log_type="governed_sync_all_shortcut",
            message="Sync All shortcut completed through switched-on fuse-box stores.",
            details=str({
                "source": shortcut_source,
                "stores_total": summary["stores_total"],
                "stores_allowed": summary["stores_allowed"],
                "stores_blocked": summary["stores_blocked"],
                "stores_failed": summary["stores_failed"],
            })[:1000],
            created_at=datetime.utcnow(),
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()

    summary["message"] = (
        f"Sync All checked {summary['stores_total']} switched-on stores. "
        f"Allowed: {summary['stores_allowed']}. "
        f"Blocked: {summary['stores_blocked']}. "
        f"Failed: {summary['stores_failed']}."
    )

    return jsonify(_governed_json_safe(summary)), 200


@governed_bp.post("/governed/stores/<int:store_id>/import")
def governed_store_import_shortcut(store_id):
    """Single governed store import shortcut.

    Page buttons are shortcuts only.
    SystemConfig fuse box is the authority.
    Store platform decides which importer is used.
    """
    from datetime import datetime
    from flask import jsonify, request
    from extensions import db
    from models import Store, SystemLog
    from services.runtime_action_guard import is_runtime_action_allowed

    body = request.get_json(silent=True) or {}
    shortcut_source = (
        body.get("shortcut_source")
        or request.headers.get("X-BT38-Shortcut")
        or "store_import_shortcut"
    )

    store = Store.query.get_or_404(store_id)

    guard = is_runtime_action_allowed(
        store=store,
        action_type="import",
        manual=True,
        context={
            "source": shortcut_source,
            "shortcut": True,
            "authority": "SystemConfig fuse box",
        },
    )

    def log_shortcut(status, message):
        try:
            db.session.add(SystemLog(
                log_type="governed_shortcut_import",
                message=message,
                details=(
                    f"store_id={store.id} platform={store.platform} "
                    f"shortcut=true source={shortcut_source} status={status} "
                    f"allowed={guard.get('allowed')} reason={guard.get('reason')}"
                )[:1000],
                created_at=datetime.utcnow(),
            ))
            db.session.commit()
        except Exception:
            db.session.rollback()

    if not guard.get("allowed"):
        log_shortcut("blocked", "Import shortcut blocked by fuse box")
        return jsonify(
            ok=False,
            success=False,
            governed=True,
            shortcut=True,
            status="blocked",
            action_type="import",
            store_id=store.id,
            platform=store.platform,
            fuse_box_checked=True,
            allowed=False,
            reason=guard.get("reason"),
            guard=guard,
        ), 200

    platform = str(store.platform or "").strip().lower()

    if "amazon" in platform:
        from services.governed_amazon_inventory_import import run_governed_amazon_inventory_import

        result = run_governed_amazon_inventory_import(store_id=store.id)
        log_shortcut("success", "Amazon import shortcut executed through fuse box")

        if isinstance(result, dict):
            result.update({
                "ok": bool(result.get("success", True)),
                "shortcut": True,
                "status": "success",
                "action_type": "import",
                "store_id": store.id,
                "platform": store.platform,
                "fuse_box_checked": True,
                "allowed": True,
                "guard": guard,
            })
            return jsonify(_governed_json_safe(result)), 200

        return jsonify(
            ok=True,
            success=True,
            governed=True,
            shortcut=True,
            result=result,
        ), 200

    if "ebay" in platform:
        from services.governed_ebay_inventory_import import run_governed_ebay_inventory_import

        result = run_governed_ebay_inventory_import(store_id=store.id)
        log_shortcut("success", "eBay variation import shortcut executed through fuse box")

        if isinstance(result, dict):
            result.update({
                "ok": bool(result.get("success", True)),
                "shortcut": True,
                "status": "success",
                "action_type": "import",
                "store_id": store.id,
                "platform": store.platform,
                "fuse_box_checked": True,
                "allowed": True,
                "guard": guard,
                "import_started": True,
                "push_started": False,
                "sync_started": False,
            })
            return jsonify(_governed_json_safe(result)), 200

        return jsonify(
            ok=True,
            success=True,
            governed=True,
            shortcut=True,
            status="success",
            action_type="import",
            store_id=store.id,
            platform=store.platform,
            fuse_box_checked=True,
            allowed=True,
            import_started=True,
            push_started=False,
            sync_started=False,
            result=result,
            guard=guard,
        ), 200

    log_shortcut("unsupported", "Import shortcut allowed but platform is unsupported")
    return jsonify(
        ok=False,
        success=False,
        governed=True,
        shortcut=True,
        status="unsupported_platform",
        action_type="import",
        store_id=store.id,
        platform=store.platform,
        fuse_box_checked=True,
        allowed=True,
        reason="No governed importer is wired for this platform.",
        guard=guard,
    ), 200


@governed_bp.post("/governed/amazon/inventory/import")
def governed_amazon_inventory_import():
    """
    Governed Amazon inventory import control point.

    Runtime rule:
    - This endpoint is a governed runtime import lane.
    - It must not require an interactive admin/operator role.
    - Fuse-box/settings still control whether the store/import is enabled.
    - AFN/FBA writes to AmazonFBAInventory only.
    """
    from flask import jsonify, request
    try:
        from models import Store

        body = request.get_json(silent=True) or {}
        store_id = body.get("store_id") or request.args.get("store_id")
        store = None

        if store_id:
            store = Store.query.get(int(store_id))
        else:
            store = (
                Store.query
                .filter(Store.platform.ilike("%amazon%"), Store.is_active == True)  # noqa: E712
                .order_by(Store.id)
                .first()
            )

        if not store:
            return jsonify(
                ok=False,
                success=False,
                governed=True,
                error="amazon_store_not_found",
                message="No active Amazon store found for governed import.",
            ), 404

        if not bool(getattr(store, "fba_import_enabled", False)):
            return jsonify(
                ok=False,
                success=False,
                governed=True,
                execution_blocked=True,
                reason="FBA import is disabled for this Amazon store.",
                store_id=getattr(store, "id", None),
                fuse_box_checked=True,
            ), 200

        from services.governed_amazon_inventory_import import run_governed_amazon_inventory_import
        result = run_governed_amazon_inventory_import(store_id=getattr(store, "id", None))

        if isinstance(result, dict):
            result.update({
                "ok": bool(result.get("success", True)),
                "governed": True,
                "runtime_import": True,
                "manual_role_required": False,
                "store_id": getattr(store, "id", None),
                "fuse_box_checked": True,
            })
            return jsonify(_governed_json_safe(result)), 200

        return jsonify(
            ok=True,
            success=True,
            governed=True,
            runtime_import=True,
            manual_role_required=False,
            result=result,
        ), 200

    except Exception as exc:
        return jsonify(
            ok=False,
            success=False,
            governed=True,
            error="amazon_import_failed",
            message=str(exc),
            instruction="Amazon import failed before completing. Check Amazon SP-API client wiring/credentials.",
        ), 500


@governed_bp.post("/governed/ebay/inventory/import")
def governed_ebay_inventory_import():
    """Governed eBay import control point.

    Phase status:
    - Fuse-box controlled.
    - Store-aware.
    - No legacy importer, worker, queue, or scheduler is called here.
    - Real eBay import must be added later behind this route only.
    """
    from flask import jsonify, request
    from models import Store
    from services.runtime_action_guard import is_runtime_action_allowed

    body = request.get_json(silent=True) or {}
    store_id = body.get("store_id") or request.args.get("store_id")

    store = None
    if store_id:
        store = Store.query.get(int(store_id))
    else:
        store = (
            Store.query
            .filter(Store.platform.ilike("%ebay%"))
            .order_by(Store.is_active.desc(), Store.id.asc())
            .first()
        )

    guard = is_runtime_action_allowed(
        store,
        action_type="import",
        manual=True,
        context={"source": "governed_ebay_inventory_import"},
    )

    if not guard.get("allowed"):
        return jsonify(
            ok=False,
            success=False,
            governed=True,
            marketplace="ebay",
            import_started=False,
            execution_started=False,
            reason=guard.get("reason"),
            guard=guard,
        ), 200

    from services.governed_ebay_inventory_import import run_governed_ebay_inventory_import

    try:
        result = run_governed_ebay_inventory_import(
            store_id=getattr(store, "id", None)
        )

        if isinstance(result, dict):
            return jsonify(_governed_json_safe(result)), 200

        return jsonify(
            ok=True,
            success=True,
            governed=True,
            marketplace="ebay",
            result=result,
        ), 200

    except Exception as exc:
        return jsonify(
            ok=False,
            success=False,
            governed=True,
            marketplace="ebay",
            error="ebay_import_failed",
            message=str(exc),
            instruction="eBay governed variation import failed.",
        ), 500


@governed_bp.route("/governed/webhooks/ebay", methods=["GET", "POST"])
def governed_webhook_ebay_ingest():
    """Compatibility route: eBay webhook uses the single governed webhook intake."""
    return governed_marketplace_webhook_intake("ebay")


@governed_bp.route("/governed/webhooks/amazon", methods=["GET", "POST"])
def governed_webhook_amazon_ingest():
    """Compatibility route: Amazon webhook uses the single governed webhook intake."""
    return governed_marketplace_webhook_intake("amazon")



@governed_bp.post("/governed/product-linking/link-listing-to-warehouse")
def governed_product_linking_link_listing_to_warehouse():
    """One clear governed Product Linking path.

    Keeps the existing proven relationship authority block,
    but moves the frontend direction away from /governed-disabled.
    No marketplace push. No FBM change. No rewrite.
    """
    return governed_disabled_action("link-listing-to-warehouse")

@governed_bp.route("/governed-disabled", defaults={"action": ""}, methods=["GET", "POST"])
@governed_bp.route("/governed-disabled/<path:action>", methods=["GET", "POST"])
def governed_disabled_action(action: str = ""):
    """Phase 2 governed bridge for old product-linking frontend calls.

    This is not a second execution authority.
    It only translates old frontend paths into governed product-linking/group behavior.

    Marketplace push still flows through:
    governed group/listing route
    -> runtime_action_guard.py
    -> SystemConfig fuse box
    -> governed_execution.py
    -> marketplace adapter
    """
    from flask import jsonify, request
    from extensions import db
    from models import MarketplaceListing, MasterProductGroup, WarehouseStock

    action = (action or "").strip("/")
    body = request.get_json(silent=True) or {}

    def blocked(message, status=409, **extra):
        payload = {
            "success": False,
            "ok": False,
            "governed": True,
            "legacy_bridge": True,
            "execution_blocked": True,
            "action": action,
            "method": request.method,
            "message": message,
        }
        payload.update(extra)
        return jsonify(payload), status

    def resolve_stock(stock_id):
        try:
            stock_id_int = int(stock_id)
        except Exception:
            return None
        return db.session.get(WarehouseStock, stock_id_int)

    def ensure_group_for_stock(stock):
        """Resolve one group authority for Product Linking.

        Product Linking is relationship-only. WarehouseStock is stock truth.
        If active listings already linked to this warehouse stock have a group,
        reuse that group instead of creating a duplicate group.
        """
        if not stock:
            return None

        if getattr(stock, "master_product_group_id", None):
            group = db.session.get(MasterProductGroup, int(stock.master_product_group_id))
            if group:
                return group

        existing_listing_group_id = (
            db.session.query(MarketplaceListing.master_product_group_id)
            .filter(MarketplaceListing.is_active == True)  # noqa: E712
            .filter(MarketplaceListing.warehouse_stock_id == stock.id)
            .filter(MarketplaceListing.master_product_group_id.isnot(None))
            .order_by(MarketplaceListing.updated_at.desc(), MarketplaceListing.id.desc())
            .limit(1)
            .scalar()
        )

        if existing_listing_group_id:
            group = db.session.get(MasterProductGroup, int(existing_listing_group_id))
            if group:
                stock.master_product_group_id = group.id
                stock.is_group_controlled = True
                if hasattr(stock, "group_controlled_at") and not stock.group_controlled_at:
                    from datetime import datetime
                    stock.group_controlled_at = datetime.utcnow()
                if hasattr(stock, "updated_at"):
                    from datetime import datetime
                    stock.updated_at = datetime.utcnow()
                group.updated_at = getattr(stock, "updated_at", None) or group.updated_at
                return group

        group = MasterProductGroup(
            display_title=(stock.product_name or stock.group_title or stock.sku or "Untitled Master Group")[:500],
            display_image_url=getattr(stock, "image_url", None),
        )
        db.session.add(group)
        db.session.flush()

        stock.master_product_group_id = group.id
        stock.is_group_controlled = True
        if hasattr(stock, "group_controlled_at") and not stock.group_controlled_at:
            from datetime import datetime
            stock.group_controlled_at = datetime.utcnow()
        if hasattr(stock, "updated_at"):
            from datetime import datetime
            stock.updated_at = datetime.utcnow()

        group.updated_at = getattr(stock, "updated_at", None) or group.updated_at
        return group

    def listing_preview_for_stock(stock):
        listings = (
            db.session.query(MarketplaceListing)
            .filter(MarketplaceListing.warehouse_stock_id == stock.id)
            .filter(MarketplaceListing.is_active == True)  # noqa: E712
            .order_by(MarketplaceListing.id)
            .all()
        )
        preview = []
        for listing in listings:
            platform = (listing.store.platform if listing.store else listing.platform or "").strip()
            channel = (listing.normalized_amazon_fulfillment_channel or "").upper()
            is_amazon = "amazon" in platform.lower()
            is_fba = is_amazon and channel not in ("MFN", "FBM", "MERCHANT")
            preview.append({
                "listing_id": listing.id,
                "sku": listing.external_sku,
                "external_id": listing.external_listing_id,
                "platform": platform,
                "store_name": listing.store.name if listing.store else None,
                "quantity": listing.effective_quantity,
                "is_fba": bool(is_fba),
                "pushable": bool((not is_fba) and listing.is_pushable),
            })
        return preview

    if action.startswith("group-push-preview/") and request.method == "GET":
        stock_id = action.rsplit("/", 1)[-1]
        stock = resolve_stock(stock_id)
        if not stock:
            return blocked("Warehouse stock was not found.", status=404, warehouse_stock_id=stock_id)

        listings = listing_preview_for_stock(stock)
        return jsonify({
            "success": True,
            "ok": True,
            "governed": True,
            "legacy_bridge": True,
            "preview_only": True,
            "warehouse_stock_id": stock.id,
            "group_id": stock.master_product_group_id,
            "sku": stock.sku,
            "target_qty": stock.sellable_quantity,
            "listings": listings,
            "listings_count": len([x for x in listings if x.get("pushable")]),
            "message": "Governed preview only. Push still requires governed fuse-box execution.",
        }), 200

    if action == "group-push" and request.method == "POST":
        stock_id = body.get("warehouse_stock_id") or body.get("stock_id")
        stock = resolve_stock(stock_id)
        if not stock:
            return blocked("Warehouse stock was not found.", status=404, warehouse_stock_id=stock_id)

        group = ensure_group_for_stock(stock)
        db.session.commit()

        from governed_group_propagation_routes import governed_group_propagate_quantity
        return governed_group_propagate_quantity(group.id)

    if action == "link-listing-to-warehouse" and request.method == "POST":
        listing_id = body.get("listing_id") or body.get("marketplace_listing_id")
        stock_id = body.get("warehouse_id") or body.get("warehouse_stock_id") or body.get("stock_id")

        listing = db.session.get(MarketplaceListing, int(listing_id)) if listing_id else None
        stock = resolve_stock(stock_id)

        if not listing:
            return blocked("Marketplace listing was not found.", status=404, listing_id=listing_id)
        if not stock:
            return blocked("Warehouse stock was not found.", status=404, warehouse_stock_id=stock_id)

        group = ensure_group_for_stock(stock)

        # Unified Product Linking authority:
        # The live UI calls link-listing-to-warehouse, so this path must also
        # merge existing group ownership into ONE MasterProductGroup.
        # Rule:
        # - standalone warehouse rows update from warehouse only
        # - grouped rows update from group authority only
        # - one physical product must not split across competing group IDs
        merge_group_ids = set()
        if getattr(stock, "master_product_group_id", None):
            merge_group_ids.add(int(stock.master_product_group_id))
        if getattr(listing, "master_product_group_id", None):
            merge_group_ids.add(int(listing.master_product_group_id))

        if merge_group_ids:
            from datetime import datetime

            for merge_group_id in sorted(merge_group_ids):

                linked_listings = (
                    db.session.query(MarketplaceListing)
                    .filter(MarketplaceListing.master_product_group_id == merge_group_id)
                    .all()
                )

                for linked_listing in linked_listings:
                    linked_listing.master_product_group_id = group.id

                    if linked_listing.id == listing.id:
                        linked_listing.warehouse_stock_id = stock.id

                    if hasattr(linked_listing, "updated_at"):
                        linked_listing.updated_at = datetime.utcnow()

        listing.warehouse_stock_id = stock.id
        listing.master_product_group_id = group.id

        # FBA-led groups must follow the same relationship pattern as working FBM groups:
        # every active listing attached to a grouped stock belongs to the group,
        # every stock used by a grouped listing belongs to the group,
        # and exactly one warehouse stock remains the group authority.
        #
        # This is local relationship repair only. It does not push, import, sync,
        # change quantities, or alter marketplace state. FBA/AFN remains read-only.
        from datetime import datetime

        now = datetime.utcnow()
        target_stock_ids = {int(stock.id)}

        group_listings_for_authority = (
            db.session.query(MarketplaceListing)
            .filter(MarketplaceListing.master_product_group_id == group.id)
            .filter(MarketplaceListing.is_active == True)  # noqa: E712
            .all()
        )

        for group_listing in group_listings_for_authority:
            if getattr(group_listing, "warehouse_stock_id", None):
                target_stock_ids.add(int(group_listing.warehouse_stock_id))

        attached_listings = (
            db.session.query(MarketplaceListing)
            .filter(MarketplaceListing.is_active == True)  # noqa: E712
            .filter(MarketplaceListing.warehouse_stock_id.in_(list(target_stock_ids)))
            .all()
        )

        for attached_listing in attached_listings:
            attached_listing.master_product_group_id = group.id
            if getattr(attached_listing, "warehouse_stock_id", None):
                target_stock_ids.add(int(attached_listing.warehouse_stock_id))
            if hasattr(attached_listing, "updated_at"):
                attached_listing.updated_at = now

        candidate_listings_by_id = {
            int(item.id): item
            for item in list(group_listings_for_authority) + list(attached_listings)
        }

        def is_read_only_fba_listing(item):
            platform = ((item.store.platform if item.store else "") or "").strip().lower()
            channel = (getattr(item, "normalized_amazon_fulfillment_channel", None) or "").strip().upper()
            return bool(getattr(item, "is_fba", False)) or (
                "amazon" in platform and channel not in ("MFN", "FBM", "MERCHANT")
            )

        fba_authority_stock_ids = {
            int(item.warehouse_stock_id)
            for item in candidate_listings_by_id.values()
            if is_read_only_fba_listing(item) and getattr(item, "warehouse_stock_id", None)
        }

        if fba_authority_stock_ids:
            authority_stock_id = sorted(fba_authority_stock_ids)[0]
        else:
            authority_stock_id = int(stock.id)

        # Group 45 rule:
        # every active grouped listing keeps its own warehouse row,
        # but every warehouse row used by the group must also belong to the same group.
        # This keeps FBA separate/read-only, keeps eBay/FBM separate/pushable,
        # and prevents half-linked groups where a listing has group_id but its stock row does not.
        for grouped_listing in (
            db.session.query(MarketplaceListing)
            .filter(MarketplaceListing.master_product_group_id == group.id)
            .filter(MarketplaceListing.is_active == True)  # noqa: E712
            .all()
        ):
            if getattr(grouped_listing, "warehouse_stock_id", None):
                target_stock_ids.add(int(grouped_listing.warehouse_stock_id))

        group_stocks = (
            db.session.query(WarehouseStock)
            .filter(WarehouseStock.id.in_(list(target_stock_ids)))
            .all()
        )

        for group_stock in group_stocks:
            group_stock.master_product_group_id = group.id
            group_stock.is_group_controlled = True
            if hasattr(group_stock, "group_controlled_at") and not group_stock.group_controlled_at:
                group_stock.group_controlled_at = now
            if hasattr(group_stock, "updated_at"):
                group_stock.updated_at = now

        if hasattr(listing, "updated_at"):
            from datetime import datetime
            listing.updated_at = datetime.utcnow()

        # Same-SKU Amazon FBA shadow rows must not remain as separate active listings/groups.
        # They are read-only quantity cache rows and cause duplicate Master Stock rows.
        duplicate_shadow_count = 0
        listing_sku = (getattr(listing, "external_sku", None) or "").strip()
        listing_fnsku = (getattr(listing, "fnsku", None) or getattr(listing, "external_listing_id", None) or "").strip()

        duplicate_query = db.session.query(MarketplaceListing).filter(
            MarketplaceListing.id != listing.id,
            MarketplaceListing.is_active == True,  # noqa: E712
        )

        if listing_sku:
            duplicate_query = duplicate_query.filter(MarketplaceListing.external_sku == listing_sku)
        elif listing_fnsku:
            duplicate_query = duplicate_query.filter(
                (MarketplaceListing.fnsku == listing_fnsku)
                | (MarketplaceListing.external_listing_id == listing_fnsku)
            )
        else:
            duplicate_query = duplicate_query.filter(False)

        for duplicate in duplicate_query.all():
            duplicate_title = (getattr(duplicate, "title", None) or "").strip().lower()
            duplicate_channel = (getattr(duplicate, "normalized_amazon_fulfillment_channel", None) or "").strip().upper()
            duplicate_platform = ((duplicate.store.platform if duplicate.store else "") or "").strip().lower()

            is_amazon_fba_duplicate = (
                "amazon" in duplicate_platform
                and duplicate_channel not in ("MFN", "FBM", "MERCHANT")
                and (
                    duplicate_title.startswith("amazon sku")
                    or not getattr(duplicate, "warehouse_stock_id", None)
                    or getattr(duplicate, "warehouse_stock_id", None) != stock.id
                )
            )

            if not is_amazon_fba_duplicate:
                continue

            duplicate.is_active = False
            duplicate.status = "archived_fba_shadow_duplicate"
            duplicate.warehouse_stock_id = stock.id
            duplicate.master_product_group_id = group.id
            if hasattr(duplicate, "updated_at"):
                from datetime import datetime
                duplicate.updated_at = datetime.utcnow()
            duplicate_shadow_count += 1

        db.session.commit()

        return jsonify({
            "success": True,
            "ok": True,
            "governed": True,
            "legacy_bridge": True,
            "listing_id": listing.id,
            "warehouse_stock_id": stock.id,
            "group_id": group.id,
            "archived_duplicate_shadow_rows": duplicate_shadow_count,
            "message": "Listing linked through governed Phase 2 bridge. Same-SKU FBA shadow duplicates were archived.",
        }), 200

    if action == "unlink-listing" and request.method == "POST":
        listing_id = body.get("listing_id") or body.get("marketplace_listing_id")
        listing = db.session.get(MarketplaceListing, int(listing_id)) if listing_id else None

        if not listing:
            return blocked("Marketplace listing was not found.", status=404, listing_id=listing_id)

        old_stock_id = listing.warehouse_stock_id
        old_group_id = listing.master_product_group_id

        # Unlinked listing rule:
        # if a marketplace listing is detached from warehouse authority,
        # it must not keep any group authority either.
        listing.warehouse_stock_id = None
        listing.master_product_group_id = None
        if hasattr(listing, "updated_at"):
            from datetime import datetime
            listing.updated_at = datetime.utcnow()

        remaining_stock_members = 0
        remaining_group_members = 0
        old_stock_is_group_controlled = None

        old_stock = None
        warehouse_group_released = False

        if old_stock_id:
            old_stock = db.session.get(WarehouseStock, int(old_stock_id))
            old_stock_is_group_controlled = bool(getattr(old_stock, "is_group_controlled", False)) if old_stock else None
            remaining_stock_members = (
                db.session.query(MarketplaceListing)
                .filter(MarketplaceListing.is_active == True)  # noqa: E712
                .filter(MarketplaceListing.warehouse_stock_id == int(old_stock_id))
                .count()
            )

        if old_group_id:
            remaining_group_members = (
                db.session.query(MarketplaceListing)
                .filter(MarketplaceListing.is_active == True)  # noqa: E712
                .filter(MarketplaceListing.master_product_group_id == int(old_group_id))
                .count()
            )

        # Release warehouse group-control only when unlink leaves no active marketplace members.
        # This is explicit governed unlink cleanup, not automatic title/quantity inference.
        if (
            old_stock is not None
            and old_group_id
            and remaining_stock_members == 0
            and remaining_group_members == 0
            and getattr(old_stock, "master_product_group_id", None) == int(old_group_id)
        ):
            old_stock.master_product_group_id = None
            old_stock.is_group_controlled = False
            if hasattr(old_stock, "updated_at"):
                old_stock.updated_at = listing.updated_at
            warehouse_group_released = True

        db.session.commit()

        return jsonify({
            "success": True,
            "ok": True,
            "governed": True,
            "legacy_bridge": True,
            "listing_id": listing.id,
            "old_warehouse_stock_id": old_stock_id,
            "old_group_id": old_group_id,
            "remaining_stock_members": remaining_stock_members,
            "remaining_group_members": remaining_group_members,
            "old_stock_is_group_controlled": old_stock_is_group_controlled,
            "warehouse_review_required": bool(old_stock_id and remaining_stock_members == 0),
            "group_review_required": bool(old_group_id and remaining_group_members <= 1),
            "warehouse_group_released": warehouse_group_released,
            "message": "Listing unlinked through governed Phase 2 bridge. Empty warehouse group-control released when safe.",
        }), 200

    if action == "product-linking-link" and request.method == "POST":
        return blocked(
            "Create-new-listing link is not enabled yet. Use existing marketplace listings and governed link-listing-to-warehouse first.",
            status=409,
            action_supported=False,
        )

    return blocked("This legacy action is disabled until a governed bridge is approved.")


@governed_bp.post("/governed/product-linking/merge-warehouse-group")
def governed_product_linking_merge_warehouse_group():
    """Governed Product Linking merge.

    Creates/reuses ONE MasterProductGroup and attaches all selected warehouse rows
    to that same group. No marketplace push. No quantity change. No delete/archive.
    """
    from datetime import datetime
    from flask import jsonify, request
    from extensions import db
    from models import MasterProductGroup, WarehouseStock, MarketplaceListing

    body = request.get_json(silent=True) or {}
    stock_ids = body.get("warehouse_stock_ids") or body.get("stock_ids") or []
    target_group_id = body.get("target_group_id")

    try:
        stock_ids = [int(x) for x in stock_ids if x is not None]
    except Exception:
        return jsonify(ok=False, success=False, governed=True, message="Invalid warehouse_stock_ids."), 400

    stock_ids = list(dict.fromkeys(stock_ids))

    if len(stock_ids) < 2:
        return jsonify(ok=False, success=False, governed=True, message="Select at least two warehouse rows to merge into one group."), 400

    stocks = (
        db.session.query(WarehouseStock)
        .filter(WarehouseStock.id.in_(stock_ids))
        .filter(WarehouseStock.is_active == True)  # noqa: E712
        .filter(WarehouseStock.is_deleted == False)  # noqa: E712
        .all()
    )

    if len(stocks) != len(stock_ids):
        return jsonify(ok=False, success=False, governed=True, message="One or more warehouse rows were not found or are inactive."), 404

    group = None
    if target_group_id:
        group = db.session.get(MasterProductGroup, int(target_group_id))

    if group is None:
        existing_group_ids = [
            int(s.master_product_group_id)
            for s in stocks
            if getattr(s, "master_product_group_id", None)
        ]
        if existing_group_ids:
            group = db.session.get(MasterProductGroup, existing_group_ids[0])

    if group is None:
        primary = stocks[0]
        group = MasterProductGroup(
            display_title=(primary.product_name or primary.group_title or primary.sku or "Untitled Master Group")[:500],
            display_image_url=getattr(primary, "image_url", None),
        )
        db.session.add(group)
        db.session.flush()

    moved_stock_ids = []
    moved_listing_ids = []

    for stock in stocks:
        stock.master_product_group_id = group.id
        stock.is_group_controlled = True
        if hasattr(stock, "group_controlled_at") and not stock.group_controlled_at:
            stock.group_controlled_at = datetime.utcnow()
        if hasattr(stock, "updated_at"):
            stock.updated_at = datetime.utcnow()
        moved_stock_ids.append(stock.id)

        linked_listings = (
            db.session.query(MarketplaceListing)
            .filter(MarketplaceListing.is_active == True)  # noqa: E712
            .filter(MarketplaceListing.warehouse_stock_id == stock.id)
            .all()
        )

        for listing in linked_listings:
            listing.master_product_group_id = group.id
            if hasattr(listing, "updated_at"):
                listing.updated_at = datetime.utcnow()
            moved_listing_ids.append(listing.id)

    group.updated_at = datetime.utcnow()
    db.session.commit()

    return jsonify(
        ok=True,
        success=True,
        governed=True,
        action="merge_warehouse_group",
        group_id=group.id,
        warehouse_stock_ids=moved_stock_ids,
        marketplace_listing_ids=moved_listing_ids,
        message="Selected warehouse rows merged into one governed product group. No marketplace push executed.",
    ), 200



@governed_bp.post("/governed/product-linking/repair/reset-failures")
def governed_product_linking_repair_reset_failures():
    from flask import jsonify
    return jsonify({
        "success": False,
        "ok": False,
        "governed": True,
        "execution_blocked": True,
        "action": "reset-failures",
        "message": "This governed repair action is disabled until approved."
    }), 409


@governed_bp.post("/governed/product-linking/repair/rebuild-link")
def governed_product_linking_repair_rebuild_link():
    from flask import jsonify
    return jsonify({
        "success": False,
        "ok": False,
        "governed": True,
        "execution_blocked": True,
        "action": "rebuild-link",
        "message": "This governed repair action is disabled until approved."
    }), 409


@governed_bp.post("/governed/product-linking/repair/create-missing-sku")
def governed_product_linking_repair_create_missing_sku():
    from flask import jsonify
    return jsonify({
        "success": False,
        "ok": False,
        "governed": True,
        "execution_blocked": True,
        "action": "create-missing-sku",
        "message": "This governed repair action is disabled until approved."
    }), 409


@governed_bp.post("/governed/product-linking/repair/sync-now")
def governed_product_linking_repair_sync_now():
    from flask import jsonify
    return jsonify({
        "success": False,
        "ok": False,
        "governed": True,
        "execution_blocked": True,
        "action": "sync-now",
        "message": "This governed repair action is disabled until approved."
    }), 409




@governed_bp.post("/governed/warehouse/<int:stock_id>/archive")
def governed_warehouse_archive_stock(stock_id: int):
    """Governed warehouse archive action.

    Soft archive only:
    - no hard delete
    - no marketplace push
    - blocked if active marketplace listings still depend on this warehouse row
    - preserves transfer/history records
    """
    from datetime import datetime
    from flask import jsonify
    from extensions import db
    from models import WarehouseStock, MarketplaceListing, MarketplaceOrder, AmazonFBAInventory

    stock = db.session.get(WarehouseStock, stock_id)
    if stock is None:
        return jsonify(
            ok=False,
            success=False,
            governed=True,
            execution_blocked=True,
            reason="Warehouse stock was not found.",
            warehouse_stock_id=stock_id,
        ), 404

    active_listings = (
        db.session.query(MarketplaceListing)
        .filter(
            MarketplaceListing.warehouse_stock_id == stock.id,
            MarketplaceListing.is_active == True,  # noqa: E712
        )
        .count()
    )

    active_orders = (
        db.session.query(MarketplaceOrder)
        .filter(
            MarketplaceOrder.warehouse_stock_id == stock.id,
            MarketplaceOrder.processed_at.is_(None),
        )
        .count()
    )

    active_fba_rows = (
        db.session.query(AmazonFBAInventory)
        .filter(
            AmazonFBAInventory.warehouse_stock_id == stock.id,
            AmazonFBAInventory.is_active == True,  # noqa: E712
            AmazonFBAInventory.is_archived == False,  # noqa: E712
        )
        .count()
    )

    if active_listings or active_orders or active_fba_rows:
        return jsonify(
            ok=False,
            success=False,
            governed=True,
            execution_blocked=True,
            reason="Warehouse stock still has active marketplace/order/FBA references and cannot be archived safely.",
            warehouse_stock_id=stock.id,
            active_listings=active_listings,
            active_orders=active_orders,
            active_fba_rows=active_fba_rows,
        ), 409

    stock.is_active = False
    stock.is_archived = True
    stock.is_deleted = True
    stock.deleted_at = datetime.utcnow()
    stock.updated_at = datetime.utcnow()

    db.session.commit()

    return jsonify(
        ok=True,
        success=True,
        governed=True,
        action="warehouse_archive",
        warehouse_stock_id=stock.id,
        sku=stock.sku,
        archived=True,
        reason="Warehouse stock soft-archived. No marketplace push executed. Transfer/history records preserved.",
    ), 200

@governed_bp.post("/governed/warehouse/stock-transfer/convert-to-fbm")
def governed_warehouse_stock_transfer_convert_to_fbm():
    """Governed warehouse action: convert a warehouse row to FBM operational control.

    This does not create a new SKU.
    This does not call Amazon/eBay.
    This records transfer movement and updates linked listing state to MFN/FBM.
    """
    from datetime import datetime
    from flask import jsonify, request
    from extensions import db
    from models import StockTransfer, WarehouseStock, MarketplaceListing, AmazonFBAInventory

    body = request.get_json(silent=True) or {}
    stock_id = body.get("warehouse_stock_id") or body.get("stock_id")
    listing_id = body.get("listing_id")
    quantity = body.get("quantity")
    reason = body.get("reason") or "Convert to FBM from warehouse pending action"
    notes = body.get("notes") or "Created from governed warehouse Convert to FBM action."

    try:
        stock_id_int = int(stock_id)
    except Exception:
        return jsonify(
            ok=False,
            success=False,
            governed=True,
            execution_blocked=True,
            reason="warehouse_stock_id is required for Convert to FBM.",
        ), 400

    stock = db.session.get(WarehouseStock, stock_id_int)
    if stock is None:
        return jsonify(
            ok=False,
            success=False,
            governed=True,
            execution_blocked=True,
            reason="Warehouse stock was not found.",
            warehouse_stock_id=stock_id_int,
        ), 404

    def first_int(*values):
        for value in values:
            try:
                parsed = int(value or 0)
            except Exception:
                parsed = 0
            if parsed > 0:
                return parsed
        return 0

    qty = first_int(
        quantity,
        getattr(stock, "sellable_quantity", None),
        getattr(stock, "available_quantity", None),
        getattr(stock, "available", None),
        getattr(stock, "quantity", None),
        getattr(stock, "qty", None),
        getattr(stock, "stock_quantity", None),
    )

    transfer = StockTransfer(
        warehouse_stock_id=stock.id,
        from_location="fba",
        to_location="warehouse",
        qty_planned=qty,
        qty_received=qty,
        qty_sellable=qty,
        qty_damaged=0,
        reason=reason,
        notes=notes,
        status="Completed",
        created_by=request.headers.get("X-Actor", "warehouse-convert-to-fbm"),
        received_by=request.headers.get("X-Actor", "warehouse-convert-to-fbm"),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        received_at=datetime.utcnow(),
    )

    listing = None
    listing_updated = False
    stale_failure_reset = False
    fba_row_archived = False

    if listing_id not in (None, "", "0"):
        try:
            listing = db.session.get(MarketplaceListing, int(listing_id))
        except Exception:
            listing = None

    if listing is not None:
        if hasattr(listing, "amazon_fulfillment_channel"):
            listing.amazon_fulfillment_channel = "MFN"

        # Converted Amazon rows must be governed by live fulfilment state, not SKU prefix.
        # A SKU starting with FBA- is pushable once the listing is MFN/FBM/MERCHANT.
        if hasattr(listing, "push_state"):
            listing.push_state = "active"
        if hasattr(listing, "last_push_status"):
            listing.last_push_status = "pending"
        if hasattr(listing, "last_sync_status"):
            listing.last_sync_status = "converted_to_fbm"
        if hasattr(listing, "last_error_message"):
            listing.last_error_message = None

        listing_updated = True

        # normalized_amazon_fulfillment_channel is a computed property.
        # Do not assign directly. It derives from underlying listing state.

        external_sku = str(getattr(listing, "external_sku", "") or "").strip()
        channel = str(
            getattr(listing, "normalized_amazon_fulfillment_channel", "") or ""
        ).strip().upper()

        transfer_confirmed = bool(
            getattr(listing, "warehouse_stock_id", None)
            and getattr(listing, "is_active", False)
            and external_sku
            and channel in {"MFN", "FBM", "MERCHANT"}
        )

        if transfer_confirmed:
            if hasattr(listing, "push_state"):
                listing.push_state = "active"

            if hasattr(listing, "consecutive_failures"):
                listing.consecutive_failures = 0

            old_error = str(getattr(listing, "last_push_error", "") or "")
            old_error_lower = old_error.lower()

            if (
                "fba/afn" in old_error_lower
                or "fba push" in old_error_lower
                or "read-only" in old_error_lower
                or "afn" in old_error_lower
            ):
                if hasattr(listing, "last_push_error"):
                    listing.last_push_error = None

            if hasattr(listing, "last_push_status"):
                listing.last_push_status = "converted_to_fbm"

            matching_fba_rows = (
                db.session.query(AmazonFBAInventory)
                .filter(AmazonFBAInventory.seller_sku == external_sku)
                .all()
            )

            for fba_row in matching_fba_rows:
                fba_row.available_quantity = 0
                fba_row.reserved_quantity = 0
                fba_row.inbound_quantity = 0
                fba_row.is_archived = True
                fba_row.is_orphaned = False
                fba_row.updated_at = datetime.utcnow()
                fba_row_archived = True

            stale_failure_reset = True
            listing_updated = True

        else:
            if hasattr(listing, "last_push_status"):
                listing.last_push_status = "transfer_pending_confirmation"

            if hasattr(listing, "last_push_error"):
                listing.last_push_error = (
                    "Transfer pending confirmation: "
                    "listing must be warehouse-linked, active, "
                    "MFN/FBM, and have stable SKU identity."
                )

            listing_updated = True

    db.session.add(transfer)
    db.session.commit()

    return jsonify(
        ok=True,
        success=True,
        governed=True,
        execution_started=False,
        marketplace_execution=False,
        action="convert_to_fbm",
        warehouse_stock_id=stock.id,
        listing_id=getattr(listing, "id", listing_id),
        sku=getattr(stock, "sku", None),
        stock_transfer_id=transfer.id,
        transfer_status=transfer.status,
        from_location=transfer.from_location,
        to_location=transfer.to_location,
        quantity=qty,
        listing_updated=listing_updated,
        stale_failure_reset=stale_failure_reset,
        fba_row_archived=fba_row_archived,
        reason="Stock transfer recorded, listing marked MFN/FBM, and matching FBA read-only row archived where confirmed. SKU identity preserved. Marketplace execution has not been started.",
    ), 200


# === BT38 PRIVATE SETTINGS COCKPIT API ===
def _bt38_settings_bool(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).strip().lower()
    return "true" if text in {"1", "true", "yes", "on", "enabled"} else "false"


def _bt38_settings_set_config(key, value):
    from app import db
    from models import SystemConfig
    cfg = SystemConfig.query.filter_by(key=key).first()
    if cfg is None:
        cfg = SystemConfig(key=key, value=str(value))
        db.session.add(cfg)
    else:
        cfg.value = str(value)
    db.session.commit()
    return cfg


@governed_bp.get("/governed/settings/state")
def governed_settings_state():
    from flask import jsonify
    from models import SystemConfig, Store

    keys = [
        "push_enabled", "runtime_push_enabled", "marketplace_push_enabled",
        "import_enabled", "runtime_import_enabled", "marketplace_import_enabled",
        "sync_enabled", "runtime_sync_enabled", "marketplace_sync_enabled",
        "manual_push_enabled", "manual_import_enabled", "manual_sync_enabled",
        "quantity_push_enabled", "price_push_enabled", "group_push_enabled", "bulk_push_enabled",
        "read_only_mode", "dry_run_mode", "queue_frozen",
        "scheduler_enabled", "sync_worker_enabled", "push_worker_enabled",
        "retry_queue_enabled", "reconcile_15m_enabled",
        "webhook_worker_enabled", "webhook_ebay_enabled", "webhook_amazon_enabled",
        "default_push_frequency_minutes", "default_batch_size", "default_retry_attempts",
        "api_rate_limit_buffer", "error_rate_threshold",
    ]

    config = {key: "false" for key in keys}
    for row in SystemConfig.query.filter(SystemConfig.key.in_(keys)).all():
        config[row.key] = str(row.value)

    stores = []
    for store in Store.query.order_by(Store.id).all():
        stores.append({
            "id": store.id,
            "name": store.name,
            "platform": store.platform,
            "store_mode": getattr(store, "store_mode", None),
            "is_active": bool(getattr(store, "is_active", False)),
            "fba_import_enabled": bool(getattr(store, "fba_import_enabled", False)),
            "fbm_sync_enabled": bool(getattr(store, "fbm_sync_enabled", False)),
            "auto_push_enabled": bool(getattr(store, "auto_push_enabled", False)),
            "sync_status": getattr(store, "sync_status", None),
            "last_sync": str(getattr(store, "last_sync", "") or ""),
        })

    try:
        from services.governed_runtime_engine import get_governed_runtime_status
        runtime_status = get_governed_runtime_status()
    except Exception:
        runtime_status = {}

    engine_running = bool(runtime_status.get("engine_started"))

    live_runtime = {
        "marketplace_execution_on_boot": engine_running,
        "workers_running": engine_running,
        "schedulers_running": engine_running,
        "queue_consumers_running": engine_running,
        "order_import_ticks_running": engine_running,
        "push_loops_running": False,
        "runtime_mode": runtime_status.get("runtime_mode") or ("AUTOMATED GOVERNED" if engine_running else "MANUAL GOVERNED"),
        "execution_mode": runtime_status.get("execution_mode") or ("AUTOMATED + MANUAL GOVERNED" if engine_running else "MANUAL ONLY"),
        "runtime_truth": "Governed runtime engine is RUNNING. Automation obeys fuse-box/store settings and FBA remains read-only." if engine_running else "Config can be ON while live workers remain NOT RUNNING. Manual governed actions can run only through the fuse-box path.",
        "automation_runtime_status": {
            "scheduler": "RUNNING" if engine_running else "NOT RUNNING",
            "sync_worker": "RUNNING" if engine_running else "NOT RUNNING",
            "push_worker": "GOVERNED QUEUE READY" if engine_running else "NOT RUNNING",
            "retry_queue": "RUNNING" if engine_running else "NOT RUNNING",
            "reconcile_15m": "RUNNING" if engine_running else "NOT RUNNING",
            "webhook_worker": "RUNNING" if engine_running else "INGESTION ONLY",
            "webhook_ebay": "GATE + GOVERNED RUNTIME" if engine_running else "GATE ONLY",
            "webhook_amazon": "GATE + GOVERNED RUNTIME" if engine_running else "GATE ONLY",
            "webhook_execution": "GOVERNED RUNTIME READY" if engine_running else "NOT WIRED",
        },
        "engine": runtime_status,
    }

    return jsonify(
        ok=True,
        success=True,
        governed=True,
        config=config,
        stores=stores,
        live_runtime=live_runtime,
    )


@governed_bp.post("/governed/settings/config")
def governed_settings_config_update():
    from flask import jsonify, request

    allowed = {
        "push_enabled", "runtime_push_enabled", "marketplace_push_enabled",
        "import_enabled", "runtime_import_enabled", "marketplace_import_enabled",
        "sync_enabled", "runtime_sync_enabled", "marketplace_sync_enabled",
        "manual_push_enabled", "manual_import_enabled", "manual_sync_enabled",
        "quantity_push_enabled", "price_push_enabled", "group_push_enabled", "bulk_push_enabled",
        "read_only_mode", "dry_run_mode", "queue_frozen",
        "scheduler_enabled", "sync_worker_enabled", "push_worker_enabled",
        "retry_queue_enabled", "reconcile_15m_enabled",
        "webhook_worker_enabled", "webhook_ebay_enabled", "webhook_amazon_enabled",
        "default_push_frequency_minutes", "default_batch_size", "default_retry_attempts",
        "api_rate_limit_buffer", "error_rate_threshold",
    }

    body = request.get_json(silent=True) or {}
    key = str(body.get("key") or "").strip()
    if key in BT38_SYNC_STRUCTURE_KEYS and not _bt38_structure_secret_ok(body):
        return _bt38_structure_lock_response()

    key = str(body.get("key", "")).strip()
    value = body.get("value")

    if key not in allowed:
        return jsonify(ok=False, success=False, governed=True, error="setting_not_allowed", key=key), 400

    numeric_settings = {
        "default_push_frequency_minutes": int,
        "default_batch_size": int,
        "default_retry_attempts": int,
        "api_rate_limit_buffer": float,
        "error_rate_threshold": float,
    }

    if key in numeric_settings:
        try:
            value = numeric_settings[key](value)
        except Exception:
            return jsonify(ok=False, success=False, governed=True, error="invalid_numeric_value", key=key, value=value), 400
    elif isinstance(value, bool):
        value = _bt38_settings_bool(value)

    _bt38_settings_set_config(key, value)
    return jsonify(ok=True, success=True, governed=True, key=key, value=str(value))


@governed_bp.post("/governed/settings/stores/<int:store_id>")
def governed_settings_store_update(store_id):
    from flask import jsonify, request
    from app import db
    from models import Store

    allowed = {
        "is_active": "bool",
        "fba_import_enabled": "bool",
        "fbm_sync_enabled": "bool",
        "auto_push_enabled": "bool",
        "store_mode": "mode",
    }

    body = request.get_json(silent=True) or {}
    field = str(body.get("field") or "").strip()
    if field in BT38_SYNC_STORE_FIELDS and not _bt38_structure_secret_ok(body):
        return _bt38_structure_lock_response()

    field = str(body.get("field", "")).strip()
    value = body.get("value")

    if field not in allowed:
        return jsonify(ok=False, success=False, governed=True, error="store_field_not_allowed", field=field), 400

    store = Store.query.get(store_id)
    if store is None:
        return jsonify(ok=False, success=False, governed=True, error="store_not_found", store_id=store_id), 404

    if allowed[field] == "bool":
        setattr(store, field, bool(value))
    else:
        mode = str(value).strip().lower()
        if mode not in {"safe", "live", "disabled"}:
            return jsonify(ok=False, success=False, governed=True, error="invalid_store_mode", value=value), 400
        setattr(store, field, mode)

    db.session.commit()
    return jsonify(ok=True, success=True, governed=True, store_id=store.id, field=field, value=getattr(store, field))


@governed_bp.post("/governed/settings/emergency-freeze")
def governed_settings_emergency_freeze():
    from flask import jsonify

    freeze = {
        "push_enabled": "false",
        "runtime_push_enabled": "false",
        "marketplace_push_enabled": "false",
        "import_enabled": "false",
        "runtime_import_enabled": "false",
        "marketplace_import_enabled": "false",
        "sync_enabled": "false",
        "runtime_sync_enabled": "false",
        "marketplace_sync_enabled": "false",
        "manual_push_enabled": "false",
        "manual_import_enabled": "false",
        "manual_sync_enabled": "false",
        "queue_frozen": "true",
        "read_only_mode": "true",
    }

    for key, value in freeze.items():
        _bt38_settings_set_config(key, value)

    return jsonify(ok=True, success=True, governed=True, emergency_freeze=True, updated=freeze)


@governed_bp.post("/governed/settings/normalize")
def governed_settings_normalize():
    from flask import jsonify
    from app import db
    from models import SystemConfig

    defaults = {
        "default_push_frequency_minutes": "15",
        "default_batch_size": "25",
        "default_retry_attempts": "3",
        "api_rate_limit_buffer": "0.8",
        "error_rate_threshold": "0.3",
    }

    updated = {}
    for key, default in defaults.items():
        row = SystemConfig.query.filter_by(key=key).first()
        current = str(row.value).strip().lower() if row else ""
        bad = current in {"", "false", "true", "none", "null", "off", "on"}
        if row is None:
            row = SystemConfig(key=key, value=default)
            db.session.add(row)
            updated[key] = default
        elif bad:
            row.value = default
            updated[key] = default

    db.session.commit()
    return jsonify(ok=True, success=True, governed=True, normalized=updated)


# ============================================================
# Governed eBay OAuth settings only
# Owner-controlled OAuth authorize/callback/token refresh path.
# Does not perform marketplace push/import/sync.
# ============================================================

@governed_bp.get("/ebay-oauth/authorize")
def governed_ebay_oauth_authorize():
    import os
    import urllib.parse
    import secrets
    from flask import jsonify, redirect, session

    client_id = os.getenv("EBAY_CLIENT_ID")
    runame = os.getenv("EBAY_RUNAME")
    scopes = os.getenv("EBAY_SCOPES") or (
        "https://api.ebay.com/oauth/api_scope "
        "https://api.ebay.com/oauth/api_scope/sell.inventory "
        "https://api.ebay.com/oauth/api_scope/sell.fulfillment "
        "https://api.ebay.com/oauth/api_scope/sell.account"
    )

    if not client_id or not runame:
        return jsonify({
            "ok": False,
            "success": False,
            "governed": True,
            "error": "missing_ebay_oauth_env",
            "missing": {
                "EBAY_CLIENT_ID": not bool(client_id),
                "EBAY_RUNAME": not bool(runame),
            },
        }), 200

    state = secrets.token_urlsafe(24)
    session["governed_ebay_oauth_state"] = state

    params = {
        "client_id": client_id,
        "redirect_uri": runame,
        "response_type": "code",
        "scope": scopes,
        "state": state,
    }

    auth_url = "https://auth.ebay.com/oauth2/authorize?" + urllib.parse.urlencode(params)

    if request.args.get("json") == "1":
        return jsonify({
            "ok": True,
            "success": True,
            "governed": True,
            "auth_url": auth_url,
            "runame": runame,
            "mode": "production",
        }), 200

    return redirect(auth_url)


@governed_bp.get("/ebay-oauth/callback")
def governed_ebay_oauth_callback():
    import os
    import json
    import base64
    import requests
    from datetime import datetime, timedelta
    from flask import jsonify, request, session, redirect
    from app import db
    from models import Store

    code = request.args.get("code")
    state = request.args.get("state")
    expected_state = session.get("governed_ebay_oauth_state")

    if not code:
        return jsonify({
            "ok": False,
            "success": False,
            "governed": True,
            "error": "missing_code",
        }), 200

    if expected_state and state and state != expected_state:
        return jsonify({
            "ok": False,
            "success": False,
            "governed": True,
            "error": "state_mismatch",
        }), 200

    client_id = os.getenv("EBAY_CLIENT_ID")
    client_secret = os.getenv("EBAY_CLIENT_SECRET")
    runame = os.getenv("EBAY_RUNAME")

    if not client_id or not client_secret or not runame:
        return jsonify({
            "ok": False,
            "success": False,
            "governed": True,
            "error": "missing_ebay_oauth_env",
            "missing": {
                "EBAY_CLIENT_ID": not bool(client_id),
                "EBAY_CLIENT_SECRET": not bool(client_secret),
                "EBAY_RUNAME": not bool(runame),
            },
        }), 200

    basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")

    resp = requests.post(
        "https://api.ebay.com/identity/v1/oauth2/token",
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": runame,
        },
        timeout=30,
    )

    try:
        token = resp.json()
    except Exception:
        token = {"raw": resp.text}

    if resp.status_code >= 300 or not token.get("access_token"):
        return jsonify({
            "ok": False,
            "success": False,
            "governed": True,
            "error": "ebay_token_exchange_failed",
            "status_code": resp.status_code,
            "response": token,
        }), 200

    store = Store.query.filter(Store.platform.ilike("%ebay%")).order_by(Store.id.desc()).first()
    if not store:
        return jsonify({
            "ok": False,
            "success": False,
            "governed": True,
            "error": "no_ebay_store_found",
        }), 200

    existing = {}
    if isinstance(store.api_key, str):
        try:
            existing = json.loads(store.api_key)
        except Exception:
            existing = {}
    elif isinstance(store.api_key, dict):
        existing = store.api_key

    now = datetime.utcnow()
    existing.update({
        "access_token": token.get("access_token"),
        "refresh_token": token.get("refresh_token") or existing.get("refresh_token"),
        "token_type": token.get("token_type"),
        "access_token_expires_at": (now + timedelta(seconds=int(token.get("expires_in", 7200)))).isoformat(),
        "refresh_token_expires_at": (
            (now + timedelta(seconds=int(token.get("refresh_token_expires_in")))).isoformat()
            if token.get("refresh_token_expires_in") else existing.get("refresh_token_expires_at")
        ),
        "app_id": existing.get("app_id") or client_id,
        "runame": runame,
        "sandbox": False,
        "oauth_source": "governed_ebay_oauth_callback",
        "connected_at": now.isoformat(),
    })

    store.api_key = json.dumps(existing)
    store.is_active = True
    store.store_mode = "live"
    db.session.commit()

    success_payload = {
        "ok": True,
        "success": True,
        "governed": True,
        "message": "eBay OAuth tokens saved to live eBay store.",
        "store_id": store.id,
        "store_name": store.name,
        "mode": "production",
    }

    if request.args.get("json") == "1":
        return jsonify(success_payload), 200

    return redirect(f"/stores?ebay_oauth=success&store_id={store.id}")


@governed_bp.post("/ebay-oauth/token")
def governed_ebay_oauth_refresh_token():
    import os
    import json
    import base64
    import requests
    from datetime import datetime, timedelta
    from flask import jsonify
    from app import db
    from models import Store

    store = Store.query.filter(Store.platform.ilike("%ebay%")).order_by(Store.id.desc()).first()
    if not store:
        return jsonify({
            "ok": False,
            "success": False,
            "governed": True,
            "error": "no_ebay_store_found",
        }), 200

    creds = {}
    if isinstance(store.api_key, str):
        try:
            creds = json.loads(store.api_key)
        except Exception:
            creds = {}
    elif isinstance(store.api_key, dict):
        creds = store.api_key

    refresh_token = creds.get("refresh_token")
    client_id = os.getenv("EBAY_CLIENT_ID") or creds.get("app_id")
    client_secret = os.getenv("EBAY_CLIENT_SECRET") or creds.get("cert_id")

    if not refresh_token or not client_id or not client_secret:
        return jsonify({
            "ok": False,
            "success": False,
            "governed": True,
            "error": "missing_refresh_credentials",
            "missing": {
                "refresh_token": not bool(refresh_token),
                "client_id": not bool(client_id),
                "client_secret": not bool(client_secret),
            },
        }), 200

    basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    scopes = os.getenv("EBAY_SCOPES") or (
        "https://api.ebay.com/oauth/api_scope "
        "https://api.ebay.com/oauth/api_scope/sell.inventory "
        "https://api.ebay.com/oauth/api_scope/sell.fulfillment "
        "https://api.ebay.com/oauth/api_scope/sell.account"
    )

    resp = requests.post(
        "https://api.ebay.com/identity/v1/oauth2/token",
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": scopes,
        },
        timeout=30,
    )

    try:
        token = resp.json()
    except Exception:
        token = {"raw": resp.text}

    if resp.status_code >= 300 or not token.get("access_token"):
        return jsonify({
            "ok": False,
            "success": False,
            "governed": True,
            "error": "ebay_refresh_failed",
            "status_code": resp.status_code,
            "response": token,
        }), 200

    now = datetime.utcnow()
    creds.update({
        "access_token": token.get("access_token"),
        "token_type": token.get("token_type"),
        "access_token_expires_at": (now + timedelta(seconds=int(token.get("expires_in", 7200)))).isoformat(),
        "oauth_source": "governed_ebay_refresh_token",
        "refreshed_at": now.isoformat(),
        "sandbox": False,
    })

    store.api_key = json.dumps(creds)
    store.is_active = True
    store.store_mode = "live"
    db.session.commit()

    return jsonify({
        "ok": True,
        "success": True,
        "governed": True,
        "message": "eBay access token refreshed.",
        "store_id": store.id,
        "store_name": store.name,
        "mode": "production",
    }), 200

# === AMAZON FBA READ ONLY STOCK PAGE ===
# Governed fuse-box path only.
# FBA/AFN inventory is imported from Amazon and never pushed from warehouse.
@governed_bp.route('/amazon-fba-stock')
def amazon_fba_stock():
    """Amazon FBA read-only stock page. FBA/AFN is imported from Amazon and never pushed from warehouse."""
    from models import db, AmazonFBAInventory, Store

    page = request.args.get('page', 1, type=int)
    search = (request.args.get('search') or '').strip()
    status_filter = request.args.get('status', 'active')

    fba_stores = Store.query.filter(
        Store.platform.ilike('amazon'),
        Store.fba_import_enabled == True
    ).all()

    no_fba_store = len(fba_stores) == 0

    query = AmazonFBAInventory.query

    if search:
        like = f'%{search}%'
        query = query.filter(db.or_(
            AmazonFBAInventory.seller_sku.ilike(like),
            AmazonFBAInventory.title.ilike(like),
            AmazonFBAInventory.asin.ilike(like),
            AmazonFBAInventory.fnsku.ilike(like)
        ))

    if status_filter == 'orphaned':
        query = query.filter(AmazonFBAInventory.is_orphaned == True)
    elif status_filter == 'archived':
        query = query.filter(AmazonFBAInventory.is_archived == True)
    elif status_filter == 'all':
        pass
    else:
        status_filter = 'active'
        query = query.filter(AmazonFBAInventory.is_archived == False)

    fba_items = query.order_by(AmazonFBAInventory.seller_sku.asc()).paginate(
        page=page,
        per_page=50,
        error_out=False
    )

    total_skus = AmazonFBAInventory.query.filter(AmazonFBAInventory.is_archived == False).count()
    total_quantity = db.session.query(db.func.coalesce(db.func.sum(AmazonFBAInventory.available_quantity), 0)).scalar() or 0
    stores_count = len(fba_stores)
    orphaned_count = AmazonFBAInventory.query.filter(AmazonFBAInventory.is_orphaned == True).count()
    archived_count = AmazonFBAInventory.query.filter(AmazonFBAInventory.is_archived == True).count()

    stats = {
        'total_skus': total_skus,
        'total_quantity': total_quantity,
        'stores_count': stores_count
    }

    return render_template(
        'amazon_fba_stock.html',
        fba_items=fba_items,
        stats=stats,
        no_fba_store=no_fba_store,
        orphaned_count=orphaned_count,
        archived_count=archived_count,
        current_search=search,
        status_filter=status_filter
    )


@governed_bp.get("/governed/audit/runtime-understanding")
@login_required
def governed_runtime_understanding_audit():
    """Read-only engine understanding audit.

    Shows what the runtime understands after sync:
    - runtime state
    - latest marketplace imports
    - order reader status
    - latest MarketplaceOrder rows
    - whether stock can be expected to match sales
    """
    from flask import jsonify
    from models import Store, SyncLog, MarketplaceOrder

    try:
        from services.governed_runtime_engine import get_governed_runtime_status
        runtime = get_governed_runtime_status()
    except Exception as exc:
        runtime = {"error": str(exc)}

    stores = []
    for store in Store.query.order_by(Store.id).all():
        latest_logs = (
            SyncLog.query
            .filter(SyncLog.store_id == store.id)
            .order_by(SyncLog.created_at.desc(), SyncLog.id.desc())
            .limit(8)
            .all()
        )

        logs = []
        order_reader_seen = False
        order_reader_wired = False

        for log in latest_logs:
            msg = str(log.message or "")
            if "governed_" in msg and "order_import" in msg:
                order_reader_seen = True
                if "reader not yet wired" not in msg:
                    order_reader_wired = True

            logs.append({
                "id": log.id,
                "status": log.status,
                "items_synced": log.items_synced,
                "message": msg,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            })

        stores.append({
            "id": store.id,
            "name": store.name,
            "platform": store.platform,
            "is_active": bool(getattr(store, "is_active", False)),
            "store_mode": getattr(store, "store_mode", None),
            "fba_import_enabled": bool(getattr(store, "fba_import_enabled", False)),
            "fbm_sync_enabled": bool(getattr(store, "fbm_sync_enabled", False)),
            "auto_push_enabled": bool(getattr(store, "auto_push_enabled", False)),
            "last_sync": str(getattr(store, "last_sync", "") or ""),
            "order_reader_seen": order_reader_seen,
            "order_reader_wired": order_reader_wired,
            "latest_logs": logs,
        })

    latest_orders = (
        MarketplaceOrder.query
        .order_by(MarketplaceOrder.created_at.desc(), MarketplaceOrder.id.desc())
        .limit(20)
        .all()
    )

    order_rows = [{
        "id": row.id,
        "store_id": row.store_id,
        "marketplace_order_id": row.marketplace_order_id,
        "marketplace_order_item_id": row.marketplace_order_item_id,
        "sku": row.sku,
        "quantity": row.quantity,
        "warehouse_stock_id": row.warehouse_stock_id,
        "status": row.status,
        "processed_at": row.processed_at.isoformat() if row.processed_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    } for row in latest_orders]

    any_order_reader_wired = any(s["order_reader_wired"] for s in stores)

    return jsonify({
        "ok": True,
        "success": True,
        "governed": True,
        "read_only": True,
        "runtime": runtime,
        "stores": stores,
        "latest_marketplace_orders": order_rows,
        "engine_understanding": {
            "inventory_runtime_running": bool(runtime.get("engine_started")),
            "order_import_path_exists": True,
            "marketplace_order_reader_wired": any_order_reader_wired,
            "can_sales_update_stock": any_order_reader_wired,
            "truth": (
                "Inventory/listing sync is running, but marketplace sales cannot update stock until a real Amazon/eBay order reader is wired."
                if not any_order_reader_wired
                else
                "Marketplace order reader is wired; sales can create MarketplaceOrder rows for governed stock mutation."
            ),
        },
    }), 200
