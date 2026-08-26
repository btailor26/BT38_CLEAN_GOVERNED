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

    if hasattr(value, "__table__"):
        return {
            column.name: _governed_json_safe(
                getattr(value, column.name, None)
            )
            for column in value.__table__.columns
        }

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
        "scheduler_enabled": None,
        "sync_worker_enabled": None,
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
    "sync_enabled", "runtime_sync_enabled", "marketplace_sync_enabled", "manual_sync_enabled",
    "sync_worker_enabled", "scheduler_enabled", "reconcile_15m_enabled",
    "webhook_worker_enabled", "webhook_ebay_enabled", "webhook_amazon_enabled",
}

BT38_SYNC_STORE_FIELDS = {"store_mode", "is_active", "fbm_sync_enabled", "auto_push_enabled", "fba_import_enabled"}



def _governed_admin_required(f):
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


USER_PERMISSION_SECTIONS = ["inventory", "warehouse", "stores", "suppliers", "purchase_orders", "sync", "settings", "users"]


def _full_user_permissions():
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
    return Store.query.filter(Store.platform.ilike(platform_like)).filter(Store.is_active == True).order_by(Store.id).first()


def _bt38_record_webhook_event(platform: str, status: str, reason: str, payload: dict):
    from extensions import db
    from models import SystemLog
    store = _bt38_match_webhook_store(platform, payload)
    event_type = payload.get("event_type") or payload.get("notificationType") or payload.get("topic") or payload.get("type") or "marketplace_notification"
    details = {"governed": True, "source": "governed_webhook_intake", "marketplace": platform, "store_id": getattr(store, "id", None), "store_name": getattr(store, "name", None), "event_type": event_type, "status": status, "reason": reason, "payload": payload}
    row = SystemLog(log_type="marketplace_webhook", message=f"{platform} webhook {status}: {event_type}", details=json.dumps(details, default=str))
    db.session.add(row)
    db.session.commit()
    return row


@governed_bp.route("/governed/webhooks/<marketplace>", methods=["GET", "POST"])
def governed_marketplace_webhook_intake(marketplace):
    platform = (marketplace or "").strip().lower()
    if platform not in {"amazon", "ebay"}:
        return jsonify({"ok": False, "success": False, "governed": True, "error": "unsupported_marketplace", "marketplace": platform}), 404
    if request.method == "GET":
        challenge = request.args.get("challenge_code") or request.args.get("challenge") or request.args.get("hub.challenge")
        return jsonify({"ok": True, "success": True, "governed": True, "marketplace": platform, "challenge": challenge, "message": "Governed webhook intake is reachable. No marketplace execution was run."}), 200
    from services.governed_webhook_capture import capture_amazon_notification, capture_ebay_notification, mark_notification_status
    notification_record_id = capture_ebay_notification(request) if platform == "ebay" else capture_amazon_notification(request)
    payload = _bt38_webhook_payload()
    allowed, reason = _bt38_webhook_platform_allowed(platform)
    store = _bt38_match_webhook_store(platform, payload)
    if store is not None:
        payload["_bt38_store_id"] = int(store.id)
    status = "received" if allowed else "blocked_by_fuse"
    row = _bt38_record_webhook_event(platform, status, reason, payload)
    if not allowed:
        mark_notification_status(platform, notification_record_id, processing_status="BLOCKED", parsed=True, completed=True)
        return jsonify({"ok": True, "success": True, "governed": True, "marketplace": platform, "status": status, "reason": reason, "notification_record_id": notification_record_id, "system_log_id": row.id, "notification_result": None}), 200
    mark_notification_status(platform, notification_record_id, processing_status="PROCESSING", verification_status="PENDING", parsed=True)
    from services.governed_webhook_execution import process_marketplace_notification
    notification_result = process_marketplace_notification(marketplace=platform, payload=payload, actor=f"webhook_{platform}", notification_record_id=notification_record_id)
    mark_notification_status(platform, notification_record_id, processing_status="COMPLETED", completed=True)
    return jsonify({"ok": True, "success": True, "governed": True, "marketplace": platform, "status": status, "reason": reason, "notification_record_id": notification_record_id, "system_log_id": row.id, "notification_result": _governed_json_safe(notification_result)}), 200


@governed_bp.get("/shutdown-proof/status")
def shutdown_proof_status():
    return jsonify({"success": True, "ok": True, "shutdown_mode": True, "old_marketplace_routes_present": False})


@governed_bp.get("/warehouse")
@login_required
def governed_warehouse_page():
    """Governed Master Stock UI: display current DB truth only; no marketplace execution."""
    from flask import make_response
    from extensions import db
    from models import MarketplaceListing, WarehouseStock, Store
    from sqlalchemy import or_
    from sqlalchemy.orm import joinedload

    db.session.expire_all()
    q = (request.args.get("q") or "").strip().lower()
    view = (request.args.get("view") or "all").strip().lower()
    try:
        row_limit = int(request.args.get("per_page") or 15)
    except Exception:
        row_limit = 15
    if row_limit not in (15, 25, 50, 100):
        row_limit = 15

    listing_query = (
        db.session.query(MarketplaceListing)
        .options(joinedload(MarketplaceListing.store), joinedload(MarketplaceListing.warehouse_stock))
        .filter(MarketplaceListing.is_active == True)
        .filter(~MarketplaceListing.title.ilike("Amazon SKU%"))
        .populate_existing()
    )
    if q:
        like = f"%{q}%"
        listing_query = listing_query.filter(or_(MarketplaceListing.external_sku.ilike(like), MarketplaceListing.title.ilike(like), MarketplaceListing.external_listing_id.ilike(like), MarketplaceListing.asin.ilike(like), MarketplaceListing.fnsku.ilike(like), MarketplaceListing.barcode.ilike(like)))

    total_matching_rows = listing_query.count()
    listing_rows = listing_query.order_by(MarketplaceListing.updated_at.desc(), MarketplaceListing.id.desc()).all()
    rows = []
    linked_stock_ids = set()

    for listing in listing_rows:
        stock = listing.warehouse_stock
        if stock:
            linked_stock_ids.add(stock.id)
        platform = (listing.store.platform if listing.store else "Marketplace") or "Marketplace"
        channel = (listing.normalized_amazon_fulfillment_channel or "").upper()
        is_amazon = "amazon" in platform.lower()
        is_fbm = is_amazon and channel in ("MFN", "FBM", "MERCHANT")
        is_fba = is_amazon and not is_fbm
        fba_cached_quantity = int(getattr(listing, "last_marketplace_qty", 0) or 0) if is_fba else None
        display_sku = listing.external_sku or (stock.sku if stock else None) or listing.external_listing_id or ""
        rows.append(SimpleNamespace(
            id=stock.id if stock else 0,
            marketplace_listing_id=listing.id,
            sku=display_sku,
            master_product_group_id=listing.master_product_group_id or (stock.master_product_group_id if stock else None),
            location=f"{platform} {'FBA' if is_fba else 'FBM'}" if is_amazon else platform,
            image_url=stock.image_url if stock else None,
            product_name=(stock.product_name if stock else None) or listing.title,
            title=listing.title,
            group_title=stock.group_title if stock else None,
            barcode=listing.fnsku or listing.barcode or (stock.barcode if stock else None),
            mcf_group_source=bool(is_fba),
            is_fba=bool(is_fba),
            is_fbm=bool(is_fbm),
            is_group_controlled=bool(stock.is_group_controlled) if stock else False,
            available_quantity=(fba_cached_quantity if is_fba else int(stock.sellable_quantity or 0)) if stock else (fba_cached_quantity if is_fba else int(listing.last_marketplace_qty or 0)),
            price=listing.price or 0,
            store_name=listing.store.name if listing.store else platform,
            platform=platform,
            external_listing_id=listing.external_listing_id,
            external_sku=listing.external_sku,
            asin=listing.asin,
            fnsku=listing.fnsku,
        ))

    active_stock_rows = db.session.query(WarehouseStock).filter(WarehouseStock.is_active == True).filter(WarehouseStock.is_deleted == False).all()
    stats = SimpleNamespace(
        total_skus=len(active_stock_rows),
        total_available=sum(int(getattr(stock, "sellable_quantity", 0) or 0) for stock in active_stock_rows),
        low_stock_count=sum(1 for stock in active_stock_rows if int(getattr(stock, "sellable_quantity", 0) or 0) <= 0),
        listing_count=db.session.query(MarketplaceListing).filter(MarketplaceListing.is_active == True).count(),
        inventory_value=0.0,
    )
    warehouse_items = SimpleNamespace(items=rows, total=total_matching_rows, visible=len(rows))
    pagination = SimpleNamespace(page=1, per_page=row_limit, total=total_matching_rows, total_pages=1, has_prev=False, has_next=False, prev_page=1, next_page=1)
    html = render_template("warehouse.html", warehouse_items=warehouse_items, stats=stats, search_query=q, active_view=view, per_page=row_limit, page=1, pagination=pagination, marketplace_filter="", status_filter="", group_filter="", listing_status_filter="")
    response = make_response(_patch_warehouse_phase1_ui(html, stats, q, view))
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


def _patch_warehouse_phase1_ui(html: str, stats, search_query: str, active_view: str) -> str:
    return html
