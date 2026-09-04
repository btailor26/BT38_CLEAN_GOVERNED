"""Final FBM presentation alignment and zero-query UI event protection.

The historical installer name is retained for compatibility, but this module now
keeps the governed FBM workflow in the intended order: Pending first, then Ready
to dispatch. It also removes the stale lifecycle suppression that excluded valid
Amazon Pending FBM rows from the existing bounded session snapshot.

The notification bell remains informational only. It renders events already
emitted by the existing governed in-memory UI event path and must never query
Neon, a marketplace, a provider, or reconstruct business truth. Canonical pages
continue to read persisted DB truth through their existing governed routes.
"""
from __future__ import annotations

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
    (("marketplace_dispatch_confirmed", "label_assigned", "dispatched", "shipped"), "Dispatched"),
    (("marketplace_sale", "sale", "order_received", "new_order", "confirmed", "unshipped", "pending"), "Sale"),
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
    order_id = str(
        event.get("order_id")
        or event.get("marketplace_order_id")
        or ""
    ).strip()
    sku = str(event.get("seller_sku") or event.get("sku") or "").strip()
    platform = str(event.get("platform") or "Marketplace").strip() or "Marketplace"
    quantity = event.get("quantity")
    carrier = str(event.get("carrier") or event.get("provider") or "").strip()
    subject = str(event.get("product_title") or sku or order_id or "Marketplace order").strip()
    title = f"{label} · {subject}"

    return {
        "event_key": f"runtime:{revision}:{_normalise(label)}:{order_id}:{sku}",
        "id": f"runtime:{revision}",
        "log_type": "marketplace_sale" if label == "Sale" else "marketplace_lifecycle",
        "platform": platform,
        "title": title,
        "message": title,
        "order_id": order_id,
        "sku": sku,
        "quantity": quantity,
        "carrier": carrier,
        "status_label": label,
        "created_at": event.get("published_at"),
    }


def _event_only_bell_reader():
    """Return recent commercial events from the existing in-memory event queue.

    This function is deliberately DB-blind. The bell is not a truth surface and
    must never discover, hydrate, verify, reconcile, or reconstruct persisted
    state. A process restart may clear bell history; canonical DB-backed pages
    remain the authority for business truth.
    """
    from services import governed_ui_event_signal as event_signal

    try:
        limit = int(request.args.get("limit") or 20)
    except Exception:
        limit = 20
    limit = max(1, min(limit, 50))

    with event_signal._condition:
        events = [dict(event) for event in list(event_signal._events)]

    records = []
    seen = set()
    for event in reversed(events):
        record = _event_to_bell_record(event)
        if record is None:
            continue
        key = str(record.get("event_key") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        records.append(record)
        if len(records) >= limit:
            break

    return jsonify({
        "success": True,
        "records": records,
        "latest_event_at": records[0].get("created_at") if records else None,
        "source": "governed_in_memory_events",
        "db_query": False,
    })


def _restore_pending_fbm_visibility() -> None:
    """Restore the base persisted FBM eligibility contract, including Pending.

    The lifecycle layer previously wrapped the page eligibility function solely
    to reject Amazon Pending rows. The current workflow requires those rows to
    appear in Pending, so the final alignment reinstates the same base eligibility
    rules without adding a reader, query, poller, or alternate snapshot path.
    """
    from services import governed_fbm_page_alignment as page

    if getattr(page, "_bt38_pending_visibility_restored", False):
        return

    def aligned_visible_eligible(row, profile=None):
        if not page._is_fbm_eligible(row):
            return False

        if page._platform(row).strip().lower() != "amazon":
            return True

        fulfillment = str(getattr(row, "fulfillment_type", "") or "").strip().upper()
        profile_channel = (
            str(getattr(profile, "fulfillment_channel", "") or "").strip().upper()
            if profile else ""
        )

        if profile_channel in {"AFN", "FBA", "MCF"}:
            return False
        if profile_channel in {"MFN", "FBM"}:
            return True
        return fulfillment in {"MFN", "FBM"}

    page._workspace_fbm_eligible = aligned_visible_eligible
    page._bt38_pending_visibility_restored = True


def _fbm_row_visibility_script() -> str:
    """Clear the stale inline display:none left by the small FBM overlay.

    The existing PageController remains the pagination/filter authority via the
    row.hidden flag. We only remove the contradictory inline display override so
    a row selected by the active queue and pager can become visible again.
    """
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
    # Pending is the first persisted marketplace state and therefore the first
    # browser workflow tab. Do not let the later compatibility overlay reverse it.
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

    # Bell state is event-driven. Page open/wake must not hydrate from Neon.
    html = html.replace("hydrateBellAfterWake();", "stale = true;")

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
        _event_only_bell_reader._bt38_zero_query_event_bell = True
        app.view_functions[bell_endpoint] = login_required(_event_only_bell_reader)

    if not getattr(app, "_bt38_db_pressure_response_alignment", False):
        app.after_request(_align_browser_pressure_response)
        app._bt38_db_pressure_response_alignment = True

    app.logger.info(
        "BT38 FBM final alignment: Pending first and visible from persisted FBM truth; stale inline row display override removed; bell remains in-memory zero-query; no committed-event full /fbm reread"
    )
