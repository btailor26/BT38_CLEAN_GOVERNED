"""Final FBM presentation alignment and authority-backed bell reminders.

The bell remains presentation only. It owns no business state and performs no
marketplace/provider calls. On explicit bell open it projects the current
persisted authorities already owned by BT38: Warehouse/listing truth for listing
reminders and FBM/order/shipment truth for dispatch/journey reminders. Existing
in-session governed events are still included for immediate movement display.

Opening the bell does not action a reminder. A reminder changes or disappears
only when its underlying authority changes (for example FBM Unshipped ->
Shipped). No worker, poller, replay scan, second event bus or notification ledger
is introduced.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from flask import jsonify, make_response, request
from flask_login import login_required


_COMMERCIAL_LABELS = (
    (("return_fulfillment_completed", "return_closed", "returned"), "Returned"),
    (("return_requested", "return_fulfillment_initiated"), "Return requested"),
    (("refund_requested",), "Refund requested"),
    (("refunded", "refund_completed", "refund_issued"), "Refunded"),
    (("cancellation_requested", "cancel_requested"), "Cancellation requested"),
    (("cancelled", "canceled"), "Cancelled"),
    (("replacement_requested",), "Replacement requested"),
    (("replacement", "replaced"), "Replacement"),
    (("chargeback",), "Chargeback"),
    (("dispute",), "Dispute"),
    (("case_open", "case_opened"), "Issue / case"),
    (("out_for_delivery",), "Out for delivery"),
    (("delivered",), "Delivered"),
    (("in_transit",), "In transit"),
    (("carrier_accepted", "picked_up", "collected"), "Picked up"),
    (("marketplace_dispatch_confirmed", "label_assigned", "dispatched", "shipped"), "Shipped"),
    (("marketplace_sale", "sale", "order_received", "new_order", "confirmed", "unshipped", "pending"), "Get ready to dispatch"),
)


def _normalise(value) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _commercial_label(event: dict) -> str | None:
    values = " ".join(
        _normalise(event.get(key))
        for key in (
            "event_type",
            "lifecycle_status",
            "status",
            "log_type",
            "source",
            "title",
            "message",
        )
        if event.get(key) not in (None, "")
    )
    if not values:
        return None
    for tokens, label in _COMMERCIAL_LABELS:
        if any(token in values for token in tokens):
            return label
    return None


def _event_to_bell_record(event: dict) -> dict | None:
    label = _commercial_label(event)
    if not label:
        return None

    revision = int(event.get("revision") or 0)
    order_id = str(event.get("order_id") or event.get("marketplace_order_id") or "").strip()
    sku = str(event.get("seller_sku") or event.get("sku") or "").strip()
    platform = str(event.get("platform") or "Marketplace").strip() or "Marketplace"
    quantity = event.get("quantity")
    carrier = str(event.get("carrier") or event.get("provider") or "").strip()
    product_title = str(event.get("product_title") or "").strip()
    subject = product_title or order_id or "Marketplace order"
    title = f"{label} · {platform} · {subject}"

    return {
        "event_key": f"runtime:{revision}:{_normalise(label)}:{order_id}:{sku}",
        "id": f"runtime:{revision}",
        "log_type": "marketplace_sale" if label == "Get ready to dispatch" else "marketplace_lifecycle",
        "platform": platform,
        "title": title,
        "message": title,
        "order_id": order_id,
        "sku": sku,
        "product_title": product_title,
        "quantity": quantity,
        "carrier": carrier,
        "status_label": label,
        "created_at": event.get("published_at"),
    }


def _authority_bell_reader():
    """Project reminders from existing authorities only when the bell is opened."""
    from extensions import db
    from fbm_models import FBMShipment
    from models import MarketplaceListing, MarketplaceOrder, Store, WarehouseStock
    from services import governed_ui_event_signal as event_signal

    try:
        limit = int(request.args.get("limit") or 20)
    except Exception:
        limit = 20
    limit = max(1, min(limit, 50))
    probe = min(150, max(limit * 4, 40))
    cutoff = datetime.utcnow() - timedelta(hours=24)

    records = []

    # FBM is the order/dispatch authority. Only orders still requiring dispatch
    # remain as dispatch reminders. Once FBM moves away from these states the old
    # reminder naturally disappears without the bell writing any state.
    actionable_statuses = ("pending", "unshipped", "confirmed", "partially_shipped")
    order_rows = (
        db.session.query(
            MarketplaceOrder.id,
            MarketplaceOrder.store_id,
            MarketplaceOrder.marketplace_order_id,
            MarketplaceOrder.marketplace_order_item_id,
            MarketplaceOrder.sku,
            MarketplaceOrder.quantity,
            MarketplaceOrder.status,
            MarketplaceOrder.created_at,
            MarketplaceOrder.updated_at,
            MarketplaceOrder.warehouse_stock_id,
            Store.platform,
            WarehouseStock.product_name,
        )
        .join(Store, Store.id == MarketplaceOrder.store_id)
        .outerjoin(WarehouseStock, WarehouseStock.id == MarketplaceOrder.warehouse_stock_id)
        .filter(MarketplaceOrder.fulfillment_type == "FBM")
        .filter(MarketplaceOrder.status.in_(actionable_statuses))
        .order_by(MarketplaceOrder.updated_at.desc(), MarketplaceOrder.id.desc())
        .limit(probe)
        .all()
    )

    order_by_identity = {}
    for row in order_rows:
        order_id = str(row.marketplace_order_id or "").strip()
        sku = str(row.sku or "").strip()
        status = _normalise(row.status)
        label = "Partially dispatched" if status == "partially_shipped" else "Get ready to dispatch"
        product_title = str(row.product_name or sku or order_id or "Order").strip()
        line_identity = str(row.marketplace_order_item_id or sku or row.id)
        changed_at = row.updated_at or row.created_at
        record = {
            "event_key": f"fbm:{row.store_id}:{order_id}:{line_identity}:{status}",
            "id": f"order:{row.id}",
            "log_type": "marketplace_sale",
            "platform": row.platform or "Marketplace",
            "title": f"{label} · {row.platform or 'Marketplace'} · {product_title}",
            "message": f"Order {order_id} · Qty {int(row.quantity or 0)}",
            "order_id": order_id,
            "sku": sku,
            "product_title": product_title,
            "quantity": int(row.quantity or 0),
            "lifecycle_status": status,
            "status_label": label,
            "created_at": changed_at.isoformat() if changed_at else None,
        }
        records.append(record)
        order_by_identity[(row.store_id, order_id)] = record

    # Current FBM journey is also authority-backed. Project one highest reached
    # milestone per shipment/order rather than replaying every historical step.
    shipment_rows = (
        db.session.query(
            FBMShipment.id,
            FBMShipment.store_id,
            FBMShipment.marketplace_order_id,
            FBMShipment.carrier,
            FBMShipment.provider,
            FBMShipment.label_purchased_at,
            FBMShipment.marketplace_confirmed_at,
            FBMShipment.carrier_accepted_at,
            FBMShipment.first_movement_at,
            FBMShipment.delivered_at,
            FBMShipment.updated_at,
            Store.platform,
        )
        .join(Store, Store.id == FBMShipment.store_id)
        .filter(FBMShipment.updated_at >= cutoff)
        .order_by(FBMShipment.updated_at.desc(), FBMShipment.id.desc())
        .limit(probe)
        .all()
    )
    milestones = (
        ("Delivered", "delivered_at"),
        ("In transit", "first_movement_at"),
        ("Picked up", "carrier_accepted_at"),
        ("Shipped", "marketplace_confirmed_at"),
        ("Shipped", "label_purchased_at"),
    )
    journey_seen = set()
    for row in shipment_rows:
        identity = (row.store_id, str(row.marketplace_order_id or "").strip())
        if identity in journey_seen:
            continue
        label = None
        changed_at = None
        for candidate, field in milestones:
            value = getattr(row, field)
            if value is not None:
                label, changed_at = candidate, value
                break
        if not label or changed_at is None:
            continue
        journey_seen.add(identity)
        # A journey state supersedes an outstanding dispatch reminder for the
        # same exact order if both rows happen to coexist during persistence.
        if identity in order_by_identity:
            records.remove(order_by_identity[identity])
        order_id = identity[1]
        carrier = str(row.carrier or row.provider or "").strip()
        records.append({
            "event_key": f"fbm-journey:{row.id}:{_normalise(label)}:{changed_at.isoformat()}",
            "id": f"shipment:{row.id}",
            "log_type": "marketplace_lifecycle",
            "platform": row.platform or "Marketplace",
            "title": f"{label} · {row.platform or 'Marketplace'} · Order {order_id}",
            "message": " · ".join(part for part in (f"Order {order_id}", f"Carrier {carrier}" if carrier else "") if part),
            "order_id": order_id,
            "carrier": carrier,
            "status_label": label,
            "created_at": changed_at.isoformat(),
        })

    # Warehouse/listing persistence is the listing authority. New listing
    # reminders are projected from the existing MarketplaceListing row; there is
    # no bell-owned listing state or notification table.
    listing_rows = (
        db.session.query(
            MarketplaceListing.id,
            MarketplaceListing.external_listing_id,
            MarketplaceListing.external_sku,
            MarketplaceListing.title,
            MarketplaceListing.created_at,
            MarketplaceListing.store_id,
            Store.platform,
        )
        .join(Store, Store.id == MarketplaceListing.store_id)
        .filter(MarketplaceListing.created_at >= cutoff)
        .order_by(MarketplaceListing.created_at.desc(), MarketplaceListing.id.desc())
        .limit(probe)
        .all()
    )
    for row in listing_rows:
        listing_id = str(row.external_listing_id or "").strip()
        sku = str(row.external_sku or "").strip()
        product_title = str(row.title or sku or listing_id or "Listing").strip()
        records.append({
            "event_key": f"listing:{row.store_id}:{row.id}:added",
            "id": f"listing:{row.id}",
            "log_type": "marketplace_listing",
            "platform": row.platform or "Marketplace",
            "title": f"Listing added · {row.platform or 'Marketplace'} · {product_title}",
            "message": f"Listing {listing_id}" if listing_id else "Listing added",
            "listing_id": listing_id,
            "sku": sku,
            "product_title": product_title,
            "status_label": "Listing added",
            "created_at": row.created_at.isoformat() if row.created_at else None,
        })

    # Preserve the already-existing exact event handoff for immediate movement
    # types that are not represented by the authority projections above. This is
    # process-local enhancement only, never the sole source of outstanding FBM
    # or listing reminders.
    with event_signal._condition:
        live_events = [dict(event) for event in list(event_signal._events)]
    for event in reversed(live_events):
        record = _event_to_bell_record(event)
        if record is not None:
            records.append(record)

    records.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    unique = []
    seen = set()
    for record in records:
        key = str(record.get("event_key") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(record)
        if len(unique) >= limit:
            break

    return jsonify({
        "success": True,
        "records": unique,
        "latest_event_at": unique[0].get("created_at") if unique else None,
        "source": "current_authority_projection",
        "bell_authority": False,
        "marketplace_calls": False,
        "polling": False,
    })


def _restore_pending_fbm_visibility() -> None:
    from services import governed_fbm_page_alignment as page

    if getattr(page, "_bt38_pending_visibility_restored", False):
        return

    def aligned_visible_eligible(row, profile=None):
        if not page._is_fbm_eligible(row):
            return False

        if page._platform(row).strip().lower() != "amazon":
            return True

        fulfillment = str(getattr(row, "fulfillment_type", "") or "").strip().upper()
        profile_channel = str(getattr(profile, "fulfillment_channel", "") or "").strip().upper() if profile else ""

        if profile_channel in {"AFN", "FBA", "MCF"}:
            return False
        if profile_channel in {"MFN", "FBM"}:
            return True
        return fulfillment in {"MFN", "FBM"}

    page._workspace_fbm_eligible = aligned_visible_eligible
    page._bt38_pending_visibility_restored = True


def _fbm_row_visibility_script() -> str:
    return r'''
<script id="bt38FbmRowVisibilityAlignment">
(function(){
  function clearStaleDisplayOverride(){
    document.querySelectorAll('tr.fbm-order-row').forEach(function(row){
      row.style.removeProperty('display');
    });
  }

  document.addEventListener('click',function(event){
    var tab=event.target&&event.target.closest?event.target.closest('.fbm-lifecycle-tab[data-fbm-tab]'):null;
    if(tab)queueMicrotask(clearStaleDisplayOverride);
  },false);

  if(document.readyState==='loading'){
    document.addEventListener('DOMContentLoaded',clearStaleDisplayOverride,{once:true});
  }else{
    clearStaleDisplayOverride();
  }
  window.addEventListener('load',clearStaleDisplayOverride,{once:true});
})();
</script>
'''


def _align_ready_landing_html(html: str) -> str:
    html = html.replace(
        "var sessionDefaults={tab:'ready_dispatch',search:'',dirty:false};",
        "var sessionDefaults={tab:'pending',search:'',dirty:false};",
    )
    html = html.replace(
        "((saved.tab&&labels[saved.tab]&&saved.tab!=='pending')?saved.tab:'ready_dispatch')",
        "(saved.tab&&labels[saved.tab]?saved.tab:'pending')",
    )
    html = html.replace(
        "(saved.tab&&labels[saved.tab]?saved.tab:'ready_dispatch')",
        "(saved.tab&&labels[saved.tab]?saved.tab:'pending')",
    )
    html = html.replace(
        "addWorkflowButton(tabBar,'ready_dispatch','Ready to dispatch');\n  addWorkflowButton(tabBar,'pending','Pending');",
        "addWorkflowButton(tabBar,'pending','Pending');\n  addWorkflowButton(tabBar,'ready_dispatch','Ready to dispatch');",
    )

    html = html.replace("hydrateBellAfterWake();", "stale = true;")

    # The bell is a reminder, not an inbox. Opening it must never mark an
    # authority-backed action as completed. Keep the existing UI/layout and make
    # the badge count equal the current reminder projection.
    html = re.sub(
        r"function updateUnread\(\) \{.*?\n        \}\n\n        function markSeen\(\) \{.*?\n        \}",
        "function updateUnread() {\n            const pending = records.length;\n            setBadge(pending);\n            setBellLight(pending > 0);\n        }\n\n        function markSeen() {\n            // Reminder resolution belongs to Warehouse/FBM authority, not the bell.\n            updateUnread();\n        }",
        html,
        count=1,
        flags=re.S,
    )

    # The crossed bell was only an empty-state decoration and looked like a mute
    # control. Use the existing normal bell glyph without changing layout.
    html = html.replace('data-feather="bell-off" class="mb-2"', 'data-feather="bell" class="mb-2"')

    if "fbm-order-row" in html and 'id="bt38FbmRowVisibilityAlignment"' not in html:
        marker = "</body>"
        script = _fbm_row_visibility_script()
        html = html.replace(marker, script + marker, 1) if marker in html else html + script
    return html


def _align_browser_pressure_response(response):
    if response.status_code != 200:
        return response

    content_type = str(response.content_type or "").lower()
    path = request.path.rstrip("/") or "/"

    if "text/html" in content_type:
        response.set_data(_align_ready_landing_html(response.get_data(as_text=True)))
        return response

    if path == "/static/js/fbm_tracking_journey.js" and "javascript" in content_type:
        if response.direct_passthrough:
            response.direct_passthrough = False
        body = response.get_data(as_text=True)
        body = body.replace(
            "window.addEventListener('bt38-marketplace-event', refreshFbmFromGovernedEvent);",
            "window.addEventListener('bt38-marketplace-event', function(){ document.documentElement.dataset.bt38FbmCommittedStateDirty='1'; });",
        )
        response.set_data(body)
    return response


def install_governed_fbm_ready_landing_alignment(app) -> None:
    _restore_pending_fbm_visibility()

    endpoint = "governed_fbm.fbm_page"
    current = app.view_functions.get(endpoint)
    if current is not None and not getattr(current, "_bt38_ready_landing_alignment", False):
        @login_required
        def ready_landing_page():
            response = make_response(current())
            if response.status_code == 200 and "text/html" in str(response.content_type or "").lower():
                response.set_data(_align_ready_landing_html(response.get_data(as_text=True)))
            return response

        ready_landing_page._bt38_ready_landing_alignment = True
        app.view_functions[endpoint] = ready_landing_page

    bell_endpoint = "governed.governed_ui_notifications"
    if bell_endpoint in app.view_functions:
        _authority_bell_reader._bt38_authority_reminder_bell = True
        app.view_functions[bell_endpoint] = login_required(_authority_bell_reader)

    if not getattr(app, "_bt38_db_pressure_response_alignment", False):
        app.after_request(_align_browser_pressure_response)
        app._bt38_db_pressure_response_alignment = True

    app.logger.info(
        "BT38 bell aligned as reminder: explicit-open projection from Warehouse/listing and FBM authorities; opening does not clear actions; no bell-owned state, polling or marketplace calls"
    )
