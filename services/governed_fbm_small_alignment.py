"""Small final FBM alignment over the already-built governed workflow.

This module does not create another order/shipment/notification system. It only
repairs handoff ordering between existing persisted authorities:
- the final notification read is wrapped again with the existing lifecycle bell;
- persisted webhook evidence is restored to that same bell;
- Amazon promise fields observed by an existing exact profile read are persisted
  into the existing FBM operational-state row;
- persisted UTC promise timestamps are rendered in Europe/London;
- FBM workflow presentation starts with marketplace Pending, then Ready to dispatch;
- marketplace return truth has its own Returns queue, separate from Refunds;
- saved QZ printer state is restored visibly and the old Packlink status reload
  is neutralised so the existing committed-event browser refresh remains owner.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flask import jsonify, make_response, request
from flask_login import login_required
from sqlalchemy import text


_LONDON = ZoneInfo("Europe/London")


def _london_datetime(value):
    if value is None or not isinstance(value, datetime):
        return value
    aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    return aware.astimezone(_LONDON)


def _install_fbm_queue_alignment() -> None:
    """Keep Pending/Ready/Returns presentation on persisted marketplace truth only."""
    from services import governed_fbm_dispatch_queue_alignment as dispatch_queue
    from services import governed_fbm_global_search_alignment as global_search

    if getattr(global_search, "_bt38_pending_returns_queue_patched", False):
        return

    replacement_terms = tuple(getattr(global_search, "_REPLACEMENT_TERMS", ("replacement", "replaced")))
    refund_terms = ("refund", "refunded", "inr", "case", "claim", "dispute", "issue")
    return_terms = ("return", "returned")

    def aligned_status_reason(status: str):
        value = str(status or "").strip().lower()
        if any(term in value for term in replacement_terms):
            return "replacements"
        if any(term in value for term in return_terms):
            return "returns"
        if any(term in value for term in refund_terms):
            return "refunds"
        return None

    global_search._status_reason = aligned_status_reason
    global_search._WORKFLOW_TABS = {
        "pending",
        "ready_dispatch",
        "dispatched",
        "sds",
        "replacements",
        "returns",
        "refunds",
    }
    dispatch_queue._WORKFLOW_LABELS = {
        "pending": "Pending",
        "ready_dispatch": "Ready to dispatch",
        "dispatched": "Dispatched",
        "cancelled": "Cancelled",
        "replacements": "Replacement",
        "returns": "Returns",
        "refunds": "Refunds",
    }
    global_search._bt38_pending_returns_queue_patched = True


def _install_promise_alignment(app) -> None:
    import services.fbm_db_delivery_promise_alignment as promise_alignment

    if not getattr(promise_alignment, "_bt38_london_promise_merge_patched", False):
        original_merge = promise_alignment._merge_promise

        def london_merge(fallback, operational):
            merged = original_merge(fallback, operational)
            if not isinstance(merged, dict):
                return merged
            for field in (
                "ship_by_at",
                "earliest_delivery_at",
                "latest_delivery_at",
            ):
                merged[field] = _london_datetime(merged.get(field))
            return merged

        promise_alignment._merge_promise = london_merge
        promise_alignment._bt38_london_promise_merge_patched = True

    promise_alignment.install_fbm_db_delivery_promise_alignment(app)


def _install_amazon_promise_persistence() -> None:
    """Persist promise fields from an already-requested exact Amazon order read."""
    import services.fbm_amazon_order_profile as amazon_profile

    if getattr(amazon_profile, "_bt38_delivery_promise_persistence_patched", False):
        return

    original_fetch = amazon_profile._fetch_order

    def aligned_fetch(store, order_id):
        payload, address_payload = original_fetch(store, order_id)
        if not isinstance(payload, dict):
            return payload, address_payload

        service = amazon_profile._text(
            payload.get("ShipmentServiceLevelCategory")
            or payload.get("ShipServiceLevel")
        )
        ship_by = amazon_profile._parse_iso(payload.get("LatestShipDate"))
        earliest = amazon_profile._parse_iso(payload.get("EarliestDeliveryDate"))
        latest = amazon_profile._parse_iso(payload.get("LatestDeliveryDate"))
        checked_at = datetime.utcnow()

        try:
            from extensions import db

            with db.session.begin_nested():
                db.session.execute(
                    text(
                        """
                        INSERT INTO fbm_order_operational_state (
                            store_id,
                            marketplace_order_id,
                            platform,
                            shipping_service,
                            ship_by_at,
                            earliest_delivery_at,
                            latest_delivery_at,
                            parcel,
                            marketplace_checked_at,
                            created_at,
                            updated_at
                        ) VALUES (
                            :store_id,
                            :order_id,
                            'amazon',
                            :shipping_service,
                            :ship_by_at,
                            :earliest_delivery_at,
                            :latest_delivery_at,
                            CAST(:parcel AS json),
                            :checked_at,
                            :checked_at,
                            :checked_at
                        )
                        ON CONFLICT (store_id, marketplace_order_id)
                        DO UPDATE SET
                            shipping_service = COALESCE(EXCLUDED.shipping_service, fbm_order_operational_state.shipping_service),
                            ship_by_at = COALESCE(EXCLUDED.ship_by_at, fbm_order_operational_state.ship_by_at),
                            earliest_delivery_at = COALESCE(EXCLUDED.earliest_delivery_at, fbm_order_operational_state.earliest_delivery_at),
                            latest_delivery_at = COALESCE(EXCLUDED.latest_delivery_at, fbm_order_operational_state.latest_delivery_at),
                            marketplace_checked_at = EXCLUDED.marketplace_checked_at,
                            updated_at = EXCLUDED.updated_at
                        """
                    ),
                    {
                        "store_id": int(store.id),
                        "order_id": str(order_id),
                        "shipping_service": service,
                        "ship_by_at": ship_by,
                        "earliest_delivery_at": earliest,
                        "latest_delivery_at": latest,
                        "parcel": "{}",
                        "checked_at": checked_at,
                    },
                )
        except Exception:
            pass

        return payload, address_payload

    amazon_profile._fetch_order = aligned_fetch
    amazon_profile._bt38_delivery_promise_persistence_patched = True


def _safe_webhook_order_id(value):
    if isinstance(value, dict):
        for key in (
            "marketplace_order_id",
            "marketplaceOrderId",
            "AmazonOrderId",
            "amazonOrderId",
            "orderId",
            "order_id",
        ):
            candidate = value.get(key)
            if candidate not in (None, ""):
                return str(candidate).strip()
        for child in value.values():
            found = _safe_webhook_order_id(child)
            if found:
                return found
    elif isinstance(value, (list, tuple)):
        for child in value:
            found = _safe_webhook_order_id(child)
            if found:
                return found
    return None


def _install_final_bell_alignment(app) -> None:
    """Keep one final bell endpoint after main.py's existing installer order."""
    from services import governed_fbm_lifecycle_alignment as lifecycle

    app._bt38_marketplace_bell_lifecycle_wrapped = False
    lifecycle._wrap_notification_bell(app)

    endpoint = "governed.governed_ui_notifications"
    current = app.view_functions.get(endpoint)
    if current is None or getattr(current, "_bt38_final_handoff_bell", False):
        return

    @login_required
    def final_handoff_bell():
        response = current()
        if isinstance(response, tuple):
            return response
        payload = response.get_json(silent=True) if hasattr(response, "get_json") else None
        if not isinstance(payload, dict) or payload.get("success") is not True:
            return response

        try:
            limit = int(request.args.get("limit") or 20)
        except Exception:
            limit = 20
        limit = max(1, min(limit, 50))

        records = list(payload.get("records") or [])

        try:
            from extensions import db
            from models import SystemLog

            logs = (
                db.session.query(SystemLog)
                .filter(SystemLog.log_type == "marketplace_webhook")
                .order_by(SystemLog.created_at.desc(), SystemLog.id.desc())
                .limit(limit)
                .all()
            )
            for log in logs:
                try:
                    details = json.loads(log.details or "{}")
                except Exception:
                    details = {}
                platform = str(details.get("marketplace") or "Marketplace").strip()
                event_type = str(details.get("event_type") or "marketplace_notification").strip()
                order_id = _safe_webhook_order_id(details.get("payload") or details)
                label = event_type.replace("_", " ").replace("-", " ").strip().title()
                records.append({
                    "event_key": f"webhook:{log.id}",
                    "id": f"webhook:{log.id}",
                    "log_type": "marketplace_webhook",
                    "platform": platform,
                    "title": label,
                    "order_id": order_id,
                    "message": f"{platform} · {label}" + (f" · {order_id}" if order_id else ""),
                    "created_at": log.created_at.isoformat() if log.created_at else None,
                })
        except Exception:
            pass

        records.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        seen = set()
        unique = []
        for record in records:
            key = str(record.get("event_key") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(record)
            if len(unique) >= limit:
                break

        payload["records"] = unique
        payload["latest_event_at"] = unique[0].get("created_at") if unique else None
        return jsonify(payload)

    final_handoff_bell._bt38_final_handoff_bell = True
    app.view_functions[endpoint] = final_handoff_bell


def _browser_alignment_script() -> str:
    return r'''
<script id="bt38FbmSmallBrowserAlignment">
(function(){
  function restoreSavedPrinter(){
    var bridge=window.BT38FBMQZ;
    var select=document.getElementById('qzPrinter');
    var status=document.getElementById('qzStatus');
    if(!bridge||typeof bridge.savedPrinter!=='function'||!select)return;
    var saved='';try{saved=String(bridge.savedPrinter()||'').trim();}catch(_){saved='';}
    if(!saved)return;
    var exists=Array.from(select.options||[]).some(function(option){return option.value===saved;});
    if(!exists){var option=document.createElement('option');option.value=saved;option.textContent=saved+' · saved';select.appendChild(option);}
    select.value=saved;
    if(status){status.className='small text-muted mt-2';status.textContent='Saved label printer: '+saved+' · Connect QZ to verify';}
  }

  function enforceActiveQueue(){
    var active=document.querySelector('.fbm-lifecycle-tab.active[data-fbm-tab]');
    if(!active)return;
    var queue=String(active.dataset.fbmTab||'');
    document.querySelectorAll('tr.fbm-order-row').forEach(function(row){
      if(String(row.dataset.fbmQueue||'')!==queue){row.hidden=true;row.style.display='none';}
    });
  }

  async function checkPacklinkWithoutReload(button){
    var shipmentId=String(button&&button.dataset&&button.dataset.shipmentId||'').trim();
    if(!shipmentId)return;
    button.disabled=true;
    try{
      var response=await fetch('/fbm/shipments/'+encodeURIComponent(shipmentId)+'/packlink/status',{credentials:'same-origin',cache:'no-store',headers:{'Accept':'application/json'}});
      var payload=await response.json().catch(function(){return {};});
      if(!response.ok||payload.success!==true)throw new Error(payload.message||('HTTP '+response.status));
      window.alert(payload.label_ready?('Packlink label ready. '+(payload.mapping_status==='under_review'?'Mapping under review.':'Shipment updated.')):(payload.message||'Packlink label is not ready yet.'));
    }catch(error){window.alert(error.message||String(error));}
    finally{button.disabled=false;}
  }

  document.addEventListener('click',function(event){
    var tab=event.target&&event.target.closest?event.target.closest('.fbm-lifecycle-tab[data-fbm-tab]'):null;
    if(tab)queueMicrotask(enforceActiveQueue);
    var button=event.target&&event.target.closest?event.target.closest('.packlink-existing-status'):null;
    if(!button)return;
    event.preventDefault();event.stopPropagation();event.stopImmediatePropagation();
    void checkPacklinkWithoutReload(button);
  },false);

  function initialise(){restoreSavedPrinter();enforceActiveQueue();}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',initialise,{once:true});
  else initialise();
  window.addEventListener('load',enforceActiveQueue,{once:true});
})();
</script>
'''


def _align_fbm_workflow_html(html: str) -> str:
    """Align the already-built inline FBM controller without a second data path."""
    html = html.replace(
        "var labels={ready_dispatch:'Ready to dispatch',pending:'Pending',dispatched:'Dispatched',cancelled:'Cancelled',replacements:'Replacement',refunds:'Refunds'};",
        "var labels={pending:'Pending',ready_dispatch:'Ready to dispatch',dispatched:'Dispatched',cancelled:'Cancelled',replacements:'Replacement',returns:'Returns',refunds:'Refunds'};",
    )
    html = html.replace(
        "var sessionDefaults={tab:'ready_dispatch',search:'',dirty:false};",
        "var sessionDefaults={tab:'pending',search:'',dirty:false};",
    )
    html = html.replace(
        "(saved.tab&&labels[saved.tab]?saved.tab:'ready_dispatch')",
        "(saved.tab&&labels[saved.tab]?saved.tab:'pending')",
    )
    html = html.replace(
        "addWorkflowButton(tabBar,'ready_dispatch','Ready to dispatch');\n  addWorkflowButton(tabBar,'pending','Pending');",
        "addWorkflowButton(tabBar,'pending','Pending');\n  addWorkflowButton(tabBar,'ready_dispatch','Ready to dispatch');",
    )
    html = html.replace(
        "addWorkflowButton(tabBar,'replacements','Replacement');\n  addWorkflowButton(tabBar,'refunds','Refunds');",
        "addWorkflowButton(tabBar,'replacements','Replacement');\n  addWorkflowButton(tabBar,'returns','Returns');\n  addWorkflowButton(tabBar,'refunds','Refunds');",
    )
    return html


def _install_final_fbm_page_overlay(app) -> None:
    endpoint = "governed_fbm.fbm_page"
    current = app.view_functions.get(endpoint)
    if current is None or getattr(current, "_bt38_small_browser_alignment", False):
        return

    @login_required
    def aligned_page():
        response = make_response(current())
        if response.status_code != 200 or "text/html" not in str(response.content_type or "").lower():
            return response
        html = _align_fbm_workflow_html(response.get_data(as_text=True))
        marker = "</body>"
        script = _browser_alignment_script()
        response.set_data(html.replace(marker, script + marker, 1) if marker in html else html + script)
        return response

    aligned_page._bt38_small_browser_alignment = True
    app.view_functions[endpoint] = aligned_page


def install_governed_fbm_small_alignment(app) -> None:
    if getattr(app, "_bt38_fbm_small_alignment_installed", False):
        return

    _install_fbm_queue_alignment()
    _install_amazon_promise_persistence()
    _install_promise_alignment(app)
    _install_final_bell_alignment(app)
    _install_final_fbm_page_overlay(app)

    app._bt38_fbm_small_alignment_installed = True
    app.logger.info(
        "BT38 small FBM alignment installed: Pending-first workflow, separate Returns, final bell lifecycle, persisted Amazon promise, London promise display, saved QZ printer and no-reload Packlink status handoff"
    )