"""Align the existing FBM workspace to the simple human workflow.

The registered /fbm page remains the one workspace and existing order table.
Workflow tabs are server-scoped through the existing persisted FBM query layer;
the browser is presentation only. Existing FBA and MCF pages remain their truth
surfaces. Cofi is the user-facing guide. No marketplace/provider/DB write is
introduced here.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlencode

from flask import make_response, redirect, request
from flask_login import login_required

from extensions import db
from models import MarketplaceOrder
from shipping_spend_models import ShippingSpendLedger
from governed_fbm_routes import _shipment_map
from services import governed_fbm_global_search_alignment as global_search


_ORDER_ID_RE = re.compile(r'data-order-id="(\d+)"')
_WORKFLOW_LABELS = {
    "ready_dispatch": "Ready to dispatch",
    "dispatched": "Dispatched",
    "replacements": "Replacement",
    "refunds": "Refunds",
}
_DISPATCHED_STATUS_TERMS = (
    "processed", "shipped", "dispatched", "delivered", "fulfilled", "completed",
)


def _visible_order_ids(html: str) -> list[int]:
    result: list[int] = []
    for raw in _ORDER_ID_RE.findall(html or ""):
        order_id = int(raw)
        if order_id not in result:
            result.append(order_id)
    return result


def _aligned_workflow_queue_for(row: MarketplaceOrder, shipment) -> str:
    """Use persisted marketplace/shipment truth; missing tracking alone never proves Ready."""
    status = str(getattr(row, "status", "") or "").strip().lower()
    reason = global_search._status_reason(status)
    if status in global_search._CANCELLED_STATUSES or status.startswith("cancel"):
        return "excluded"
    if reason:
        return reason
    if global_search._sds_committed(shipment):
        return "dispatched"
    dispatched = bool(
        any(term in status for term in _DISPATCHED_STATUS_TERMS)
        or getattr(row, "tracking_number", None)
        or getattr(row, "shipped_at", None)
        or (shipment and getattr(shipment, "tracking_number", None))
        or (shipment and getattr(shipment, "label_purchased_at", None))
        or (shipment and getattr(shipment, "carrier_accepted_at", None))
        or (shipment and getattr(shipment, "first_movement_at", None))
        or (shipment and getattr(shipment, "delivered_at", None))
    )
    return "dispatched" if dispatched else "ready_dispatch"


# Keep the existing persisted search/tab layer as the single authority, but correct
# its classifier before any request snapshot is built.
global_search.workflow_queue_for = _aligned_workflow_queue_for
workflow_counts = global_search.workflow_counts
workflow_queue_for = global_search.workflow_queue_for


def _presentation(rows: list[MarketplaceOrder]) -> dict[str, dict]:
    shipments = _shipment_map(rows)
    shipment_ids = [shipment.id for shipment in shipments.values() if shipment and getattr(shipment, "id", None)]
    spend_by_shipment: dict[int, ShippingSpendLedger] = {}
    if shipment_ids:
        spend_rows = (
            db.session.query(ShippingSpendLedger)
            .filter(ShippingSpendLedger.confirmed.is_(True))
            .filter(ShippingSpendLedger.shipment_id.in_(shipment_ids))
            .order_by(ShippingSpendLedger.recorded_at.desc(), ShippingSpendLedger.id.desc())
            .all()
        )
        for spend in spend_rows:
            if spend.shipment_id not in spend_by_shipment:
                spend_by_shipment[int(spend.shipment_id)] = spend

    payload: dict[str, dict] = {}
    for row in rows:
        key = (row.store_id, row.marketplace_order_id)
        shipment = shipments.get(key)
        queue = workflow_queue_for(row, shipment)
        spend = spend_by_shipment.get(int(shipment.id)) if shipment and getattr(shipment, "id", None) else None
        payload[str(row.id)] = {
            "queue": queue,
            "status": str(getattr(row, "status", "") or "").strip().lower(),
            "shipping_cost": float(spend.amount) if spend is not None else None,
            "shipping_currency": str(spend.currency or "GBP").upper() if spend is not None else None,
            "shipping_cost_confirmed": spend is not None,
        }
    return payload


def _authoritative_counts() -> dict[str, int]:
    """Return persisted DB-backed workflow counts for the current request scope."""
    try:
        counts = workflow_counts()
    except Exception:
        counts = {}
    return {name: int(counts.get(name, 0) or 0) for name in _WORKFLOW_LABELS}


def _fba_count() -> int:
    """Read-only FBA/AFN order count for the badge; FBA remains outside FBM actions."""
    try:
        return int(
            db.session.query(MarketplaceOrder.id)
            .filter(db.func.upper(db.func.coalesce(MarketplaceOrder.fulfillment_type, "")).in_(("FBA", "AFN")))
            .count()
        )
    except Exception:
        return 0


def _align_cofi_ui(html: str) -> str:
    replacements = {
        'alt="BT38 shipping guide"': 'alt="Cofi"',
        "BT38 will keep the queue visible.": "Cofi will keep the queue visible.",
        "Everything that needs a shipping action is clear for this period.": "Everything that needs a shipping action is clear. Cofi will keep watching the work queue.",
        "Work through the important shipping actions first.": "Cofi has put the important shipping actions first.",
        ">Ready to Ship<": ">Ready to dispatch<",
        "Ready to Ship or Shipping options.": "Ready to dispatch or Shipping options.",
    }
    for old, new in replacements.items():
        html = html.replace(old, new)
    return html


def _inject(html: str, payload: dict[str, dict], counts: dict[str, int], fba_count: int) -> str:
    html = _align_cofi_ui(html)
    data = json.dumps(payload, separators=(",", ":"), sort_keys=True).replace("</", "<\\/")
    count_data = json.dumps(counts, separators=(",", ":"), sort_keys=True).replace("</", "<\\/")
    marker = "</body>"
    block = f'''<style id="bt38FbmLifecycleTabsAlignment">
.fbm-lifecycle-tabs{{display:flex;gap:.35rem;overflow-x:auto;padding:.45rem .5rem;border-bottom:1px solid #dee2e6;background:var(--bs-body-bg,#fff);scrollbar-width:thin}}.fbm-lifecycle-tab{{white-space:nowrap;border:1px solid #d0d5dd;background:transparent;border-radius:.375rem;padding:.38rem .62rem;font-size:.78rem;font-weight:650;color:inherit;text-decoration:none}}.fbm-lifecycle-tab:hover{{color:inherit}}.fbm-lifecycle-tab.active{{background:#212529;color:#fff;border-color:#212529}}.fbm-lifecycle-tab .badge{{margin-left:.3rem;font-size:.62rem}}.fbm-shipping-cost{{white-space:nowrap;font-weight:650}}.fbm-shipping-cost-pending{{font-size:.72rem;color:#667085;white-space:nowrap}}@media(max-width:767.98px){{.fbm-lifecycle-tabs{{padding:.4rem}}.fbm-lifecycle-tab{{padding:.34rem .5rem}}}}
</style>
<script id="bt38FbmLifecycleTabsData" type="application/json">{data}</script>
<script id="bt38FbmLifecycleCountsData" type="application/json">{count_data}</script>
<script id="bt38FbmLifecycleTabsScript">
(function(){{
  var table=document.querySelector('.fbm-orders-table');
  var dataNode=document.getElementById('bt38FbmLifecycleTabsData');
  var countNode=document.getElementById('bt38FbmLifecycleCountsData');
  if(!table||!dataNode||!countNode) return;
  var data={{}}, counts={{}}; try{{data=JSON.parse(dataNode.textContent||'{{}}');counts=JSON.parse(countNode.textContent||'{{}}')}}catch(e){{return;}}
  var card=table.closest('.card'); if(!card) return;
  var body=table.querySelector('tbody');
  var rows=Array.from(body.querySelectorAll('tr.fbm-order-row'));
  var params=new URLSearchParams(window.location.search);
  var active=params.get('fbm_tab')||'';
  var labels={{ready_dispatch:'Ready to dispatch',dispatched:'Dispatched',replacements:'Replacement',refunds:'Refunds'}};

  /* One search authority only: persisted server-side search. */
  var globalSearch=document.getElementById('bt38FbmGlobalSearch');
  document.querySelectorAll('input[type="search"]').forEach(function(input){{
    if(globalSearch && !globalSearch.contains(input)){{var host=input.closest('form')||input.parentElement;if(host) host.style.display='none';}}
  }});

  /* Remove legacy summary wording that conflicts with the workflow badges. */
  document.querySelectorAll('.fbm-overview-grid .fbm-stat-card').forEach(function(node){{node.style.display='none';}});
  document.querySelectorAll('.fbm-health-copy .small').forEach(function(node){{if(/mapping review/i.test(node.textContent||'')) node.style.display='none';}});

  function ensureCostHeader(){{var head=table.querySelector('thead tr');if(!head) return;if(head.querySelector('[data-fbm-shipping-cost="1"]')) return;var th=document.createElement('th');th.textContent='Shipping cost';th.dataset.fbmShippingCost='1';head.insertBefore(th,head.lastElementChild);}}
  function addCostCell(row,info){{if(row.querySelector('[data-fbm-shipping-cost="1"]')) return;var td=document.createElement('td');td.dataset.fbmShippingCost='1';if(info.shipping_cost_confirmed){{td.className='fbm-shipping-cost';try{{td.textContent=new Intl.NumberFormat('en-GB',{{style:'currency',currency:info.shipping_currency||'GBP'}}).format(info.shipping_cost);}}catch(e){{td.textContent=(info.shipping_currency||'GBP')+' '+Number(info.shipping_cost).toFixed(2);}}}}else{{td.className='fbm-shipping-cost-pending';td.textContent='Pending / unavailable';}}row.insertBefore(td,row.lastElementChild);}}
  ensureCostHeader();
  rows.forEach(function(row){{var info=data[row.dataset.orderId]||{{queue:active||'ready_dispatch'}};row.dataset.fbmQueue=info.queue;if(info.status==='processed'){{row.querySelectorAll('span.text-muted').forEach(function(node){{if((node.textContent||'').trim()==='Unshipped') node.textContent='Marketplace says shipped';}});}}addCostCell(row,info);}});

  function workflowHref(name){{var next=new URLSearchParams(window.location.search);next.set('fbm_tab',name);next.delete('status');next.delete('limit');return '/fbm?'+next.toString();}}
  function addWorkflowLink(bar,name,label){{var link=document.createElement('a');link.className='fbm-lifecycle-tab'+(active===name?' active':'');link.href=workflowHref(name);link.setAttribute('role','tab');link.setAttribute('aria-selected',active===name?'true':'false');link.innerHTML=label+' <span class="badge bg-light text-dark border">'+Number(counts[name]||0)+'</span>';bar.appendChild(link);}}
  function addTruthLink(bar,label,href,count){{var link=document.createElement('a');link.className='fbm-lifecycle-tab';link.href=href;link.innerHTML=label+' <span class="badge bg-light text-dark border">'+Number(count||0)+'</span>';link.title=label+' truth';bar.appendChild(link);}}

  var tabBar=document.createElement('div');tabBar.className='fbm-lifecycle-tabs';tabBar.setAttribute('role','tablist');tabBar.setAttribute('aria-label','Order workflow');
  addWorkflowLink(tabBar,'ready_dispatch','Ready to dispatch');
  addWorkflowLink(tabBar,'dispatched','Dispatched');
  addTruthLink(tabBar,'FBA','/governed/amazon-fba-stock',{int(fba_count)});
  addWorkflowLink(tabBar,'replacements','Replacement');
  addWorkflowLink(tabBar,'refunds','Refunds');
  var header=card.querySelector('.card-header');if(header) header.insertAdjacentElement('afterend',tabBar);else card.insertBefore(tabBar,card.firstChild);

  var title=card.querySelector('.card-header .fw-semibold');if(title&&labels[active]) title.textContent=labels[active];
  var actionable=active==='ready_dispatch';
  var actionArea=document.getElementById('readyToShipSelected');
  var selectAll=document.getElementById('selectAllOrders');
  if(actionArea) actionArea.classList.toggle('d-none',!actionable);
  if(selectAll) selectAll.disabled=!actionable;
  rows.forEach(function(row){{var cb=row.querySelector('.fbm-order-checkbox');if(cb) cb.closest('td').classList.toggle('invisible',!actionable);var option=row.querySelector('.fbm-shipping-options');if(option) option.classList.toggle('d-none',!actionable);}});
}})();
</script>'''
    return html.replace(marker, block + marker, 1) if marker in html else html + block


def _default_ready_redirect():
    if request.args.get("fbm_tab") or request.args.get("search") or request.args.get("q") or request.args.get("status"):
        return None
    args = request.args.to_dict(flat=True)
    args["fbm_tab"] = "ready_dispatch"
    return redirect(f"{request.path}?{urlencode(args)}")


def install_governed_fbm_dispatch_queue_alignment(app) -> None:
    if getattr(app, "_bt38_fbm_dispatch_queue_alignment_installed", False):
        return
    endpoint = "governed_fbm.fbm_page"
    current_view = app.view_functions.get(endpoint)
    if current_view is None:
        raise RuntimeError("governed FBM page endpoint is not registered")

    @login_required
    def aligned_fbm_page():
        default_redirect = _default_ready_redirect()
        if default_redirect is not None:
            return default_redirect

        original = current_view()
        response = make_response(original)
        if response.status_code != 200 or not response.mimetype.startswith("text/html"):
            return response
        html = response.get_data(as_text=True)
        order_ids = _visible_order_ids(html)
        rows: list[MarketplaceOrder] = []
        if order_ids:
            found = db.session.query(MarketplaceOrder).filter(MarketplaceOrder.id.in_(order_ids)).all()
            by_id = {row.id: row for row in found}
            rows = [by_id[order_id] for order_id in order_ids if order_id in by_id]
        response.set_data(_inject(html, _presentation(rows), _authoritative_counts(), _fba_count()))
        return response

    app.view_functions[endpoint] = aligned_fbm_page
    app._bt38_fbm_dispatch_queue_alignment_installed = True
    app.logger.info("BT38 FBM aligned: Ready/Dispatched/FBA/Replacement/Refunds; persisted truth; one search; manual shipping preserved; no parallel workflow")
