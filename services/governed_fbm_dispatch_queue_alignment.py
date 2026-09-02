"""Align the existing FBM workspace to the BT38 browser-session workflow.

The registered /fbm page remains the one workspace and existing order table.
Warehouse is the reference model: one governed DB snapshot is rendered, then
workflow tabs, search and pagination stay browser-local for that session. Existing
FBA and MCF pages remain their truth surfaces. No marketplace/provider/DB write is
introduced here.
"""
from __future__ import annotations

import json

from flask import g, make_response
from flask_login import login_required

from extensions import db
from models import MarketplaceOrder
from shipping_spend_models import ShippingSpendLedger
from services import governed_fbm_global_search_alignment as global_search


_WORKFLOW_LABELS = {
    "ready_dispatch": "Ready to dispatch",
    "dispatched": "Dispatched",
    "replacements": "Replacement",
    "refunds": "Refunds",
}
_DISPATCHED_STATUS_TERMS = (
    "shipped", "dispatched", "delivered", "fulfilled", "completed",
)


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


# Marketplace lifecycle truth remains the classifier. Only the way the browser
# consumes that already-persisted truth changes in this alignment.
global_search.workflow_queue_for = _aligned_workflow_queue_for
workflow_queue_for = global_search.workflow_queue_for


def _presentation(rows: list[MarketplaceOrder]) -> dict[str, dict]:
    from services import governed_fbm_page_alignment as page_alignment

    shipments = page_alignment._shipment_map(rows)
    shipment_ids = sorted({
        int(shipment.id)
        for shipment in shipments.values()
        if shipment and getattr(shipment, "id", None)
    })
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
            shipment_id = int(spend.shipment_id)
            if shipment_id not in spend_by_shipment:
                spend_by_shipment[shipment_id] = spend

    payload: dict[str, dict] = {}
    for row in rows:
        key = (int(row.store_id), str(row.marketplace_order_id))
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


def _counts_from_payload(payload: dict[str, dict]) -> dict[str, int]:
    counts = {name: 0 for name in _WORKFLOW_LABELS}
    for info in payload.values():
        queue = str(info.get("queue") or "")
        if queue in counts:
            counts[queue] += 1
    return counts


def _fba_count() -> int:
    """One compact aggregate read, matching the Warehouse KPI pattern."""
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


def _inject(html: str, payload: dict[str, dict], counts: dict[str, int], fba_count: int, truncated: bool) -> str:
    html = _align_cofi_ui(html)
    data = json.dumps(payload, separators=(",", ":"), sort_keys=True).replace("</", "<\\/")
    count_data = json.dumps(counts, separators=(",", ":"), sort_keys=True).replace("</", "<\\/")
    truncated_flag = "true" if truncated else "false"
    marker = "</body>"
    block = f'''<style id="bt38FbmLifecycleTabsAlignment">
.fbm-lifecycle-tabs{{display:flex;gap:.35rem;overflow-x:auto;padding:.45rem .5rem;border-bottom:1px solid #dee2e6;background:var(--bs-body-bg,#fff);scrollbar-width:thin}}.fbm-lifecycle-tab{{white-space:nowrap;border:1px solid #d0d5dd;background:transparent;border-radius:.375rem;padding:.38rem .62rem;font-size:.78rem;font-weight:650;color:inherit;text-decoration:none}}.fbm-lifecycle-tab:hover{{color:inherit}}.fbm-lifecycle-tab.active{{background:#212529;color:#fff;border-color:#212529}}.fbm-lifecycle-tab .badge{{margin-left:.3rem;font-size:.62rem}}.fbm-shipping-cost{{white-space:nowrap;font-weight:650}}.fbm-shipping-cost-pending{{font-size:.72rem;color:#667085;white-space:nowrap}}.bt38-fbm-session-page{{display:flex;align-items:center;gap:.45rem;padding:.5rem;font-size:.75rem;color:#667085}}.bt38-fbm-session-page button{{border:1px solid #d0d5dd;background:#fff;border-radius:.35rem;padding:.25rem .5rem}}@media(max-width:767.98px){{.fbm-lifecycle-tabs{{padding:.4rem}}.fbm-lifecycle-tab{{padding:.34rem .5rem}}}}
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
  var labels={{ready_dispatch:'Ready to dispatch',dispatched:'Dispatched',replacements:'Replacement',refunds:'Refunds'}};
  var sessionDefaults={{tab:'ready_dispatch',search:'',page:1,perPage:15,dirty:false}};
  var saved=(window.BT38&&typeof window.BT38.getPageSession==='function')?window.BT38.getPageSession('fbm',sessionDefaults):sessionDefaults;
  var params=new URLSearchParams(window.location.search);
  var legacyTab=params.get('fbm_tab');
  var legacySearch=params.get('search')||params.get('q');
  var active=(legacyTab&&labels[legacyTab])?legacyTab:(saved.tab&&labels[saved.tab]?saved.tab:'ready_dispatch');
  var search=String(legacySearch!=null?legacySearch:(saved.search||'')).trim().toLowerCase();
  var page=Math.max(1,Number(saved.page||1));
  var perPage=[15,25,50,100].indexOf(Number(saved.perPage))>=0?Number(saved.perPage):15;

  function saveSession(extra){{
    var next=Object.assign({{tab:active,search:search,page:page,perPage:perPage,dirty:false}},extra||{{}});
    if(window.BT38&&typeof window.BT38.setPageSession==='function') window.BT38.setPageSession('fbm',next);
    return next;
  }}

  var globalSearch=document.getElementById('bt38FbmGlobalSearch');
  var searchInput=document.getElementById('bt38FbmGlobalSearchInput');
  var clearSearch=document.getElementById('bt38FbmGlobalSearchClear');
  if(searchInput) searchInput.value=search;
  document.querySelectorAll('input[type="search"]').forEach(function(input){{
    if(globalSearch && !globalSearch.contains(input)){{var host=input.closest('form')||input.parentElement;if(host) host.style.display='none';}}
  }});

  document.querySelectorAll('.fbm-overview-grid .fbm-stat-card').forEach(function(node){{node.style.display='none';}});
  document.querySelectorAll('.fbm-health-copy .small').forEach(function(node){{if(/mapping review/i.test(node.textContent||'')) node.style.display='none';}});

  function ensureCostHeader(){{var head=table.querySelector('thead tr');if(!head) return;if(head.querySelector('[data-fbm-shipping-cost="1"]')) return;var th=document.createElement('th');th.textContent='Shipping cost';th.dataset.fbmShippingCost='1';head.insertBefore(th,head.lastElementChild);}}
  function addCostCell(row,info){{if(row.querySelector('[data-fbm-shipping-cost="1"]')) return;var td=document.createElement('td');td.dataset.fbmShippingCost='1';if(info.shipping_cost_confirmed){{td.className='fbm-shipping-cost';try{{td.textContent=new Intl.NumberFormat('en-GB',{{style:'currency',currency:info.shipping_currency||'GBP'}}).format(info.shipping_cost);}}catch(e){{td.textContent=(info.shipping_currency||'GBP')+' '+Number(info.shipping_cost).toFixed(2);}}}}else{{td.className='fbm-shipping-cost-pending';td.textContent='Pending / unavailable';}}row.insertBefore(td,row.lastElementChild);}}
  ensureCostHeader();
  rows.forEach(function(row){{var info=data[row.dataset.orderId]||{{queue:'ready_dispatch'}};row.dataset.fbmQueue=info.queue;row.dataset.fbmSearch=(row.textContent||'').toLowerCase();addCostCell(row,info);}});

  function addWorkflowButton(bar,name,label){{var button=document.createElement('button');button.type='button';button.dataset.fbmTab=name;button.className='fbm-lifecycle-tab'+(active===name?' active':'');button.setAttribute('role','tab');button.setAttribute('aria-selected',active===name?'true':'false');button.innerHTML=label+' <span class="badge bg-light text-dark border">'+Number(counts[name]||0)+'</span>';button.addEventListener('click',function(){{active=name;page=1;saveSession();render();}});bar.appendChild(button);}}
  function addTruthLink(bar,label,href,count){{var link=document.createElement('a');link.className='fbm-lifecycle-tab';link.href=href;link.innerHTML=label+' <span class="badge bg-light text-dark border">'+Number(count||0)+'</span>';link.title=label+' truth';bar.appendChild(link);}}

  var tabBar=document.createElement('div');tabBar.className='fbm-lifecycle-tabs';tabBar.setAttribute('role','tablist');tabBar.setAttribute('aria-label','Order workflow');
  addWorkflowButton(tabBar,'ready_dispatch','Ready to dispatch');
  addWorkflowButton(tabBar,'dispatched','Dispatched');
  addTruthLink(tabBar,'FBA','/governed/amazon-fba-stock',{int(fba_count)});
  addWorkflowButton(tabBar,'replacements','Replacement');
  addWorkflowButton(tabBar,'refunds','Refunds');
  var header=card.querySelector('.card-header');if(header) header.insertAdjacentElement('afterend',tabBar);else card.insertBefore(tabBar,card.firstChild);

  var pager=document.createElement('div');pager.className='bt38-fbm-session-page';
  pager.innerHTML='<button type="button" data-dir="prev">Previous</button><span data-page-status></span><button type="button" data-dir="next">Next</button><select aria-label="Orders per page"><option>15</option><option>25</option><option>50</option><option>100</option></select>';
  var select=pager.querySelector('select');select.value=String(perPage);select.addEventListener('change',function(){{perPage=Number(select.value)||15;page=1;saveSession();render();}});
  pager.querySelector('[data-dir="prev"]').addEventListener('click',function(){{if(page>1){{page-=1;saveSession();render();}}}});
  pager.querySelector('[data-dir="next"]').addEventListener('click',function(){{page+=1;saveSession();render();}});
  card.appendChild(pager);

  function render(){{
    var matched=rows.filter(function(row){{return row.dataset.fbmQueue===active&&(!search||String(row.dataset.fbmSearch||'').indexOf(search)>=0);}});
    var pages=Math.max(1,Math.ceil(matched.length/perPage));page=Math.min(Math.max(page,1),pages);
    var start=(page-1)*perPage,end=start+perPage,visible=new Set(matched.slice(start,end));
    rows.forEach(function(row){{row.hidden=!visible.has(row);}});
    tabBar.querySelectorAll('[data-fbm-tab]').forEach(function(button){{var selected=button.dataset.fbmTab===active;button.classList.toggle('active',selected);button.setAttribute('aria-selected',selected?'true':'false');}});
    var title=card.querySelector('.card-header .fw-semibold');if(title&&labels[active]) title.textContent=labels[active];
    var actionable=active==='ready_dispatch';
    var actionArea=document.getElementById('readyToShipSelected');var selectAll=document.getElementById('selectAllOrders');
    if(actionArea) actionArea.classList.toggle('d-none',!actionable);if(selectAll) selectAll.disabled=!actionable;
    rows.forEach(function(row){{var cb=row.querySelector('.fbm-order-checkbox');if(cb){{cb.checked=false;cb.closest('td').classList.toggle('invisible',!actionable);}}var option=row.querySelector('.fbm-shipping-options');if(option) option.classList.toggle('d-none',!actionable);}});
    var status=pager.querySelector('[data-page-status]');if(status)status.textContent=(matched.length?('Showing '+(start+1)+'-'+Math.min(end,matched.length)+' of '+matched.length):'Showing 0 of 0')+' loaded FBM orders · Page '+page+({truncated_flag}?' · session snapshot bounded':'');
    pager.querySelector('[data-dir="prev"]').disabled=page<=1;pager.querySelector('[data-dir="next"]').disabled=page>=pages;
    saveSession();
  }}

  if(globalSearch) globalSearch.addEventListener('submit',function(event){{event.preventDefault();search=String(searchInput&&searchInput.value||'').trim().toLowerCase();page=1;saveSession();render();}});
  if(searchInput) searchInput.addEventListener('input',function(){{search=String(searchInput.value||'').trim().toLowerCase();page=1;saveSession();render();}});
  if(clearSearch) clearSearch.addEventListener('click',function(){{if(searchInput)searchInput.value='';search='';page=1;saveSession();render();}});

  render();
}})();
</script>'''
    return html.replace(marker, block + marker, 1) if marker in html else html + block


def install_governed_fbm_dispatch_queue_alignment(app) -> None:
    if getattr(app, "_bt38_fbm_dispatch_queue_alignment_installed", False):
        return
    endpoint = "governed_fbm.fbm_page"
    current_view = app.view_functions.get(endpoint)
    if current_view is None:
        raise RuntimeError("governed FBM page endpoint is not registered")

    @login_required
    def aligned_fbm_page():
        original = current_view()
        response = make_response(original)
        if response.status_code != 200 or not response.mimetype.startswith("text/html"):
            return response

        rows = list(getattr(g, "_bt38_fbm_session_rows", []) or [])
        if not rows:
            rows, _ = global_search._session_snapshot_rows()
        truncated = bool(getattr(g, "_bt38_fbm_session_truncated", False))
        payload = _presentation(rows)
        response.set_data(_inject(
            response.get_data(as_text=True),
            payload,
            _counts_from_payload(payload),
            _fba_count(),
            truncated,
        ))
        return response

    app.view_functions[endpoint] = aligned_fbm_page
    app._bt38_fbm_dispatch_queue_alignment_installed = True
    app.logger.info("BT38 FBM aligned to Warehouse session model: one snapshot; local Ready/Dispatched/search/page; manual shipping preserved")
