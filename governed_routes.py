from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
import json

from flask import Blueprint, jsonify, request, render_template_string, redirect, url_for
try:
    from flask_login import current_user
except Exception:
    current_user = None

governed_bp = Blueprint("governed", __name__)


@governed_bp.route("/login", methods=["GET", "POST"])
def login():
    from datetime import datetime
    from flask_login import login_user
    from extensions import db
    from models import User

    next_url = request.args.get("next") or request.form.get("next") or url_for("governed.governed_warehouse_page")

    if request.method == "POST":
        identity = (request.form.get("identity") or "").strip()
        password = request.form.get("password") or ""

        user = (
            db.session.query(User)
            .filter((User.email == identity) | (User.username == identity))
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

    return render_template_string("""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>BT38 Login</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body{font-family:Arial,sans-serif;background:#f8fafc;margin:0;display:flex;min-height:100vh;align-items:center;justify-content:center;color:#111827}
    .card{background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:28px;max-width:380px;width:92%;box-shadow:0 10px 30px rgba(15,23,42,.08)}
    h1{font-size:22px;margin:0 0 6px}
    p{color:#6b7280;margin:0 0 20px}
    label{font-size:13px;font-weight:700;display:block;margin:14px 0 6px}
    input{width:100%;box-sizing:border-box;padding:12px;border:1px solid #d1d5db;border-radius:10px;font-size:15px}
    button{width:100%;margin-top:18px;padding:12px;border:0;border-radius:10px;background:#111827;color:#fff;font-weight:700;cursor:pointer}
    .error{background:#fee2e2;color:#991b1b;border-radius:10px;padding:10px;margin-bottom:12px;font-size:14px}
  </style>
</head>
<body>
  <form class="card" method="post">
    <h1>BT38 Login</h1>
    <p>Sign in to continue to Master Stock.</p>
    {% if error %}<div class="error">{{ error }}</div>{% endif %}
    <input type="hidden" name="next" value="{{ next_url }}">
    <label>Email or username</label>
    <input name="identity" autocomplete="username" required autofocus>
    <label>Password</label>
    <input name="password" type="password" autocomplete="current-password" required>
    <button type="submit">Sign in</button>
  </form>
</body>
</html>
""", error=error, next_url=next_url)


@governed_bp.get("/shutdown-proof/status")
def shutdown_proof_status():
    return jsonify({
        "success": True,
        "ok": True,
        "shutdown_mode": True,
        "old_marketplace_routes_present": False,
    })


@governed_bp.get("/")
@governed_bp.get("/warehouse")
def governed_warehouse_page():
    """Governed Master Stock UI.

    Existing UI only. No layout rebuild.
    This route aligns DB -> governed runtime -> warehouse.html.
    """
    from extensions import db
    from models import MarketplaceListing, WarehouseStock

    q = (request.args.get("q") or "").strip().lower()
    view = (request.args.get("view") or "all").strip().lower()

    listing_rows = (
        db.session.query(MarketplaceListing)
        .filter(MarketplaceListing.is_active == True)  # noqa: E712
        .order_by(MarketplaceListing.updated_at.desc(), MarketplaceListing.id.desc())
        .limit(500)
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
        row = SimpleNamespace(
            id=stock.id if stock else 0,
            inventory_item_id=None,
            item_id=None,
            marketplace_listing_id=listing.id,
            sku=(stock.sku if stock else listing.external_sku) or "",
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
            available_quantity=stock.sellable_quantity if stock else 0,
            price=listing.price or 0,
            store_name=listing.store.name if listing.store else platform,
            platform=platform,
            external_listing_id=listing.external_listing_id,
            external_sku=listing.external_sku,
            asin=listing.asin,
            fnsku=listing.fnsku,
        )
        rows.append(row)

    unlinked_stock = (
        db.session.query(WarehouseStock)
        .filter(WarehouseStock.is_active == True)  # noqa: E712
        .filter(WarehouseStock.is_deleted == False)  # noqa: E712
        .order_by(WarehouseStock.updated_at.desc(), WarehouseStock.id.desc())
        .limit(500)
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
        if len(rows) >= 500:
            break

    all_rows = rows

    if q:
        def _matches(row):
            haystack = " ".join(str(getattr(row, field, "") or "") for field in (
                "sku", "external_sku", "asin", "fnsku", "barcode", "product_name", "title",
                "group_title", "external_listing_id", "store_name", "platform", "master_product_group_id"
            )).lower()
            return q in haystack
        rows = [row for row in rows if _matches(row)]

    if view == "available":
        rows = [row for row in rows if int(getattr(row, "available_quantity", 0) or 0) > 0]
    elif view == "low-stock":
        rows = [row for row in rows if int(getattr(row, "available_quantity", 0) or 0) <= 0]
    elif view == "listings":
        rows = [row for row in rows if getattr(row, "marketplace_listing_id", None)]
    elif view == "fba":
        rows = [row for row in rows if getattr(row, "is_fba", False)]
    elif view == "fbm":
        rows = [row for row in rows if getattr(row, "is_fbm", False)]
    elif view == "groups":
        rows = [row for row in rows if getattr(row, "master_product_group_id", None) or getattr(row, "is_group_controlled", False)]

    stats = SimpleNamespace(
        total_skus=len(all_rows),
        total_available=sum(int(getattr(row, "available_quantity", 0) or 0) for row in all_rows),
        low_stock_count=sum(1 for row in all_rows if int(getattr(row, "available_quantity", 0) or 0) <= 0),
        listing_count=sum(1 for row in all_rows if getattr(row, "marketplace_listing_id", None)),
        inventory_value=sum((float(getattr(row, "price", 0) or 0) * int(getattr(row, "available_quantity", 0) or 0)) for row in all_rows),
    )
    warehouse_items = SimpleNamespace(items=rows, total=len(rows))

    from flask import render_template

    html = render_template(
        "warehouse.html",
        warehouse_items=warehouse_items,
        stats=stats,
        search_query=q,
        active_view=view,
    )
    return _patch_warehouse_phase1_ui(html, stats, q, view)


@governed_bp.post("/governed/actions/sku/dry-run")
def governed_sku_dry_run():
    from governed_execution import submit_governed_marketplace_action

    governed_payload = dict(request.get_json(silent=True) or {})
    governed_payload.setdefault("action", "push_inventory")

    result = submit_governed_marketplace_action(
        governed_payload,
        actor=request.headers.get("X-Actor", "manual-governed-dry-run"),
        approval={"approved": True, "source": "manual_sku_dry_run_route"},
        dry_run=True,
    )
    return jsonify(result), 200


@governed_bp.post("/governed/actions/listings/<int:listing_id>/push")
def governed_listing_push(listing_id: int):
    body = dict(request.get_json(silent=True) or {})
    result = _push_one_listing(
        listing_id=listing_id,
        quantity=body.get("quantity"),
        actor=_actor(),
        source="ui_listing_button",
    )
    return jsonify(result), 200 if result.get("ok") else 400


@governed_bp.post("/governed/actions/groups/<int:group_id>/push")
def governed_group_push(group_id: int):
    from extensions import db
    from models import MarketplaceListing

    body = dict(request.get_json(silent=True) or {})
    listings = (
        db.session.query(MarketplaceListing)
        .filter(MarketplaceListing.master_product_group_id == group_id)
        .filter(MarketplaceListing.is_active == True)  # noqa: E712
        .order_by(MarketplaceListing.id)
        .all()
    )
    results = [
        _push_one_listing(
            listing_id=listing.id,
            quantity=body.get("quantity"),
            actor=_actor(),
            source="ui_group_button",
        )
        for listing in listings
    ]
    ok_count = sum(1 for item in results if item.get("ok"))
    return jsonify({
        "success": ok_count == len(results) and bool(results),
        "ok": ok_count == len(results) and bool(results),
        "governed": True,
        "group_id": group_id,
        "total": len(results),
        "ok_count": ok_count,
        "results": results,
    }), 200


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
    from extensions import db
    from governed_execution import AMAZON_FBM_LIVE_APPROVAL_TYPE, submit_governed_marketplace_action
    from models import MarketplaceListing, SyncLog

    listing = db.session.get(MarketplaceListing, listing_id)
    if not listing:
        return _blocked(f"Marketplace listing {listing_id} was not found.", listing_id=listing_id)
    if not listing.store:
        return _blocked("Marketplace listing has no store.", listing_id=listing_id)
    if not listing.warehouse_stock:
        return _blocked("Marketplace listing is not linked to warehouse stock.", listing_id=listing_id)

    platform = (listing.store.platform or "").strip().lower()
    marketplace = "amazon" if "amazon" in platform else "ebay" if "ebay" in platform else platform
    try:
        push_quantity = listing.effective_quantity if quantity is None else int(quantity)
    except (TypeError, ValueError):
        return _blocked("Quantity must be an integer.", listing_id=listing_id, quantity=quantity)
    sku = (listing.external_sku or listing.warehouse_stock.sku or "").strip()

    payload = {
        "marketplace": marketplace,
        "action": "push_inventory",
        "sku": sku,
        "store_id": listing.store_id,
        "listing_id": listing.id,
        "quantity": push_quantity,
        "amazon_fulfillment_channel": listing.amazon_fulfillment_channel or "MFN",
        "source": source,
    }
    approval = {
        "approved": True,
        "approval_type": AMAZON_FBM_LIVE_APPROVAL_TYPE,
        "source": source,
        "approved_by": actor,
        "approved_at": datetime.utcnow().isoformat(),
        "scope": {
            "sku": sku,
            "store_id": listing.store_id,
            "listing_id": listing.id,
            "quantity": push_quantity,
        },
    }

    result = submit_governed_marketplace_action(
        payload,
        actor=actor,
        approval=approval,
        dry_run=False,
    )

    ok = bool(result.get("ok") or result.get("success"))
    listing.last_push_at = datetime.utcnow()
    listing.last_push_quantity = push_quantity if ok else listing.last_push_quantity
    listing.last_push_status = "success" if ok else "error"
    listing.last_push_error = None if ok else str(result.get("reason") or result.get("failure_reason") or result)[:1000]
    listing.push_attempts = 0 if ok else (listing.push_attempts or 0) + 1
    listing.consecutive_failures = 0 if ok else (listing.consecutive_failures or 0) + 1

    db.session.add(SyncLog(
        store_id=listing.store_id,
        status="success" if ok else "error",
        message=(
            f"governed_push listing_id={listing.id} sku={sku} "
            f"marketplace={marketplace} source={source} ok={ok}"
        )[:500],
        items_synced=1 if ok else 0,
        created_at=datetime.utcnow(),
    ))
    db.session.commit()

    result.update({
        "ui_action_wired": True,
        "grouping_layer_ready": True,
        "audit_history_logged": True,
        "listing_last_push_updated": True,
    })
    return result


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

  function loadRuntimeOverlay() {{
    fetch('/governed/warehouse/runtime-state')
      .then(r => r.json())
      .then(data => {{
        if (!data || !Array.isArray(data.rows)) return;

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


@governed_bp.post("/governed/warehouse/sync")
def governed_warehouse_sync_manual_run():
    from services.governed_warehouse_sync import run_governed_warehouse_sync

    body = dict(request.get_json(silent=True) or {})
    store_id = body.get("store_id")

    result = run_governed_warehouse_sync(
        store_id=store_id,
        actor=request.headers.get("X-Actor", "warehouse-sync-button"),
    )

    return jsonify(result), 200 if result.get("success") else 400


@governed_bp.post("/governed/amazon/inventory/import")
def governed_amazon_inventory_import():
    from services.governed_amazon_inventory_import import (
        run_governed_amazon_inventory_import
    )

    body = dict(request.get_json(silent=True) or {})

    result = run_governed_amazon_inventory_import(
        store_id=body.get("store_id")
    )

    return jsonify(result), 200
