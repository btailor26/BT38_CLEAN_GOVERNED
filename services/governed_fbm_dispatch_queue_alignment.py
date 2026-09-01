"""Align the existing FBM workspace to the simple human workflow.

Presentation/read-only only. The registered FBM page remains the one workspace and
existing order table. Existing FBA and MCF pages remain their truth surfaces; FBM
only links to them as shortcuts. No marketplace/provider/DB write is introduced.
"""
from __future__ import annotations

import json
import re

from flask import make_response
from flask_login import login_required

from extensions import db
from models import MarketplaceOrder
from shipping_spend_models import ShippingSpendLedger
from governed_fbm_routes import _shipment_map


_ORDER_ID_RE = re.compile(r'data-order-id="(\d+)"')
_CANCELLED_STATUSES = {"cancelled", "canceled", "cancelled_by_buyer", "cancelled_by_seller"}
_REPLACEMENT_TERMS = ("replacement", "replaced")
_REFUND_TERMS = ("refund", "refunded", "return", "returned", "inr", "case", "claim", "dispute", "issue")


def _visible_order_ids(html: str) -> list[int]:
    result: list[int] = []
    for raw in _ORDER_ID_RE.findall(html or ""):
        order_id = int(raw)
        if order_id not in result:
            result.append(order_id)
    return result


def _status_reason(status: str) -> str | None:
    value = str(status or "").strip().lower()
    if any(term in value for term in _REPLACEMENT_TERMS):
        return "replacements"
    if any(term in value for term in _REFUND_TERMS):
        return "refunds"
    return None


def _presentation(rows: list[MarketplaceOrder]) -> dict[str, dict]:
    shipments = _shipment_map(rows)
    shipment_ids = [shipment.id for shipment in shipments.values() if shipment and shipment.id]
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
        dispatched = bool(
            getattr(row, "tracking_number", None)
            or getattr(row, "shipped_at", None)
            or (shipment and getattr(shipment, "tracking_number", None))
        )
        status = str(getattr(row, "status", "") or "").strip().lower()
        reason = _status_reason(status)
        if status in _CANCELLED_STATUSES or status.startswith("cancel"):
            queue = "excluded"
        elif reason:
            queue = reason
        elif dispatched:
            queue = "dispatched"
        else:
            queue = "ready_dispatch"

        spend = spend_by_shipment.get(int(shipment.id)) if shipment and shipment.id else None
        payload[str(row.id)] = {
            "queue": queue,
            "status": status,
            "shipping_cost": float(spend.amount) if spend is not None else None,
            "shipping_currency": str(spend.currency or "GBP").upper() if spend is not None else None,
            "shipping_cost_confirmed": spend is not None,
        }
    return payload


def _inject(html: str, payload: dict[str, dict]) -> str:
    data = json.dumps(payload, separators=(",", ":"), sort_keys=True).replace("</", "<\\/")
    marker = "</body>"
    block = f'''<style id="bt38FbmLifecycleTabsAlignment">
.fbm-lifecycle-tabs{{display:flex;gap:.35rem;overflow-x:auto;padding:.45rem .5rem;border-bottom:1px solid #dee2e6;background:var(--bs-body-bg,#fff);scrollbar-width:thin}}.fbm-lifecycle-tab{{white-space:nowrap;border:1px solid #d0d5dd;background:transparent;border-radius:.375rem;padding:.38rem .62rem;font-size:.78rem;font-weight:650;color:inherit;text-decoration:none}}.fbm-lifecycle-tab:hover{{color:inherit}}.fbm-lifecycle-tab.active{{background:#212529;color:#fff;border-color:#212529}}.fbm-lifecycle-tab .badge{{margin-left:.3rem;font-size:.62rem}}.fbm-shipping-cost{{white-space:nowrap;font-weight:650}}.fbm-shipping-cost-pending{{font-size:.72rem;color:#667085;white-space:nowrap}}.fbm-tab-empty{{padding:1.2rem;text-align:center;color:#667085;font-size:.82rem}}@media(max-width:767.98px){{.fbm-lifecycle-tabs{{padding:.4rem}}.fbm-lifecycle-tab{{padding:.34rem .5rem}}}}
</style>
<script id="bt38FbmLifecycleTabsData" type="application/json">{data}</script>
<script id="bt38FbmLifecycleTabsScript">
(function(){{
  var table=document.querySelector('.fbm-orders-table');
  var dataNode=document.getElementById('bt38FbmLifecycleTabsData');
  if(!table||!dataNode) return;
  var data={{}}; try{{data=JSON.parse(dataNode.textContent||'{{}}')}}catch(e){{return;}}
  var card=table.closest('.card'); if(!card) return;
  var body=table.querySelector('tbody');
  var rows=Array.from(body.querySelectorAll('tr.fbm-order-row'));

  function ensureCostHeader(){{var head=table.querySelector('thead tr');if(!head) return;if(head.querySelector('[data-fbm-shipping-cost="1"]')) return;var th=document.createElement('th');th.textContent='Shipping cost';th.dataset.fbmShippingCost='1';head.insertBefore(th,head.lastElementChild);}}
  function addCostCell(row,info){{if(row.querySelector('[data-fbm-shipping-cost="1"]')) return;var td=document.createElement('td');td.dataset.fbmShippingCost='1';if(info.shipping_cost_confirmed){{td.className='fbm-shipping-cost';try{{td.textContent=new Intl.NumberFormat('en-GB',{{style:'currency',currency:info.shipping_currency||'GBP'}}).format(info.shipping_cost);}}catch(e){{td.textContent=(info.shipping_currency||'GBP')+' '+Number(info.shipping_cost).toFixed(2);}}}}else{{td.className='fbm-shipping-cost-pending';td.textContent='Pending / unavailable';}}row.insertBefore(td,row.lastElementChild);}}
  ensureCostHeader();
  rows.forEach(function(row){{var info=data[row.dataset.orderId]||{{queue:'ready_dispatch'}};row.dataset.fbmQueue=info.queue;addCostCell(row,info);}});

  var filters=[['ready_dispatch','Ready to dispatch'],['dispatched','Dispatched'],['replacements','Replacements'],['refunds','Refunds']];
  var tabBar=document.createElement('div');tabBar.className='fbm-lifecycle-tabs';tabBar.setAttribute('role','tablist');tabBar.setAttribute('aria-label','FBM workflow');
  filters.forEach(function(def){{var count=rows.filter(function(row){{return row.dataset.fbmQueue===def[0];}}).length;var button=document.createElement('button');button.type='button';button.className='fbm-lifecycle-tab';button.dataset.fbmTab=def[0];button.setAttribute('role','tab');button.innerHTML=def[1]+' <span class="badge bg-light text-dark border">'+count+'</span>';tabBar.appendChild(button);}});
  [['FBA','/governed/amazon-fba-stock'],['MCF','/orders-mcf']].forEach(function(def){{var link=document.createElement('a');link.className='fbm-lifecycle-tab';link.href=def[1];link.textContent=def[0];link.title=def[0]+' truth';tabBar.appendChild(link);}});
  var header=card.querySelector('.card-header');if(header) header.insertAdjacentElement('afterend',tabBar);else card.insertBefore(tabBar,card.firstChild);
  var title=card.querySelector('.card-header .fw-semibold');
  var actionArea=document.getElementById('readyToShipSelected');
  var selectAll=document.getElementById('selectAllOrders');
  var selectedBadge=document.getElementById('selectedOrderCount');
  var empty=document.createElement('div');empty.className='fbm-tab-empty d-none';empty.textContent='No loaded orders in this section.';table.closest('.table-responsive').insertAdjacentElement('afterend',empty);

  function resetSelection(){{rows.forEach(function(row){{var cb=row.querySelector('.fbm-order-checkbox');if(cb) cb.checked=false;}});if(selectAll) selectAll.checked=false;if(selectedBadge) selectedBadge.textContent='0 selected';if(actionArea) actionArea.disabled=true;}}
  function showTab(name){{resetSelection();var visible=0;rows.forEach(function(row){{var show=row.dataset.fbmQueue===name;row.classList.toggle('d-none',!show);if(show) visible++;}});Array.from(tabBar.querySelectorAll('[data-fbm-tab]')).forEach(function(btn){{var active=btn.dataset.fbmTab===name;btn.classList.toggle('active',active);btn.setAttribute('aria-selected',active?'true':'false');}});var match=filters.filter(function(def){{return def[0]===name;}})[0];if(title&&match) title.textContent=match[1];empty.classList.toggle('d-none',visible!==0);var actionable=name==='ready_dispatch';if(actionArea){{actionArea.classList.toggle('d-none',!actionable);var label=actionArea.childNodes[actionArea.childNodes.length-1];if(label&&label.nodeType===3) label.nodeValue=' Ready to dispatch';}}if(selectAll) selectAll.disabled=!actionable;rows.forEach(function(row){{var cb=row.querySelector('.fbm-order-checkbox');if(cb) cb.closest('td').classList.toggle('invisible',!actionable);var option=row.querySelector('.fbm-shipping-options');if(option) option.classList.toggle('d-none',!actionable);}});}}
  tabBar.addEventListener('click',function(event){{var btn=event.target.closest('[data-fbm-tab]');if(btn) showTab(btn.dataset.fbmTab);}});
  showTab('ready_dispatch');
}})();
</script>'''
    return html.replace(marker, block + marker, 1) if marker in html else html + block


def install_governed_fbm_dispatch_queue_alignment(app) -> None:
    """Wrap the existing governed FBM page with simple read-only workflow tabs."""
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
        html = response.get_data(as_text=True)
        order_ids = _visible_order_ids(html)
        if not order_ids:
            return response
        rows = db.session.query(MarketplaceOrder).filter(MarketplaceOrder.id.in_(order_ids)).all()
        by_id = {row.id: row for row in rows}
        ordered_rows = [by_id[order_id] for order_id in order_ids if order_id in by_id]
        response.set_data(_inject(html, _presentation(ordered_rows)))
        return response

    app.view_functions[endpoint] = aligned_fbm_page
    app._bt38_fbm_dispatch_queue_alignment_installed = True
    app.logger.info("BT38 FBM workflow aligned: existing Ready/Dispatched plus FBA/MCF truth shortcuts; no parallel workflow")
