"""Present the existing FBM workspace as actionable and dispatched queues.

This alignment is presentation/read-only. It reuses the registered governed FBM
page, MarketplaceOrder/FBMShipment truth, and confirmed ShippingSpendLedger
records. It preserves the existing FBM order card and its controls; only rows are
separated so completed dispatch history cannot clutter active shipping work.
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


def _visible_order_ids(html: str) -> list[int]:
    result: list[int] = []
    for raw in _ORDER_ID_RE.findall(html or ""):
        order_id = int(raw)
        if order_id not in result:
            result.append(order_id)
    return result


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
        if dispatched:
            queue = "dispatched"
        elif status in _CANCELLED_STATUSES or status.startswith("cancel"):
            queue = "excluded"
        else:
            queue = "needs_dispatch"

        spend = spend_by_shipment.get(int(shipment.id)) if shipment and shipment.id else None
        payload[str(row.id)] = {
            "queue": queue,
            "shipping_cost": float(spend.amount) if spend is not None else None,
            "shipping_currency": str(spend.currency or "GBP").upper() if spend is not None else None,
            "shipping_cost_confirmed": spend is not None,
        }
    return payload


def _inject(html: str, payload: dict[str, dict]) -> str:
    data = json.dumps(payload, separators=(",", ":"), sort_keys=True).replace("</", "<\\/")
    marker = "</body>"
    block = f'''<style id="bt38FbmDispatchQueueAlignment">
.fbm-queue-caption{{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:.55rem .75rem;border-top:1px solid #e1e5eb;background:#f8fafc}}.fbm-queue-caption strong{{font-size:.9rem}}.fbm-queue-caption span{{font-size:.72rem;color:#667085}}.fbm-dispatch-history{{margin-top:.8rem}}.fbm-dispatch-history .card-header{{padding:.55rem .75rem}}.fbm-dispatch-history .table-responsive{{border-radius:0 0 .375rem .375rem}}.fbm-shipping-cost{{white-space:nowrap;font-weight:650}}.fbm-shipping-cost-pending{{font-size:.72rem;color:#667085;white-space:nowrap}}
</style>
<script id="bt38FbmDispatchQueueAlignmentData" type="application/json">{data}</script>
<script id="bt38FbmDispatchQueueAlignmentScript">
(function(){{
  var source=document.querySelector('.fbm-orders-table');
  var dataNode=document.getElementById('bt38FbmDispatchQueueAlignmentData');
  if(!source||!dataNode) return;
  var data={{}}; try{{data=JSON.parse(dataNode.textContent||'{{}}')}}catch(e){{return;}}
  var card=source.closest('.card'); if(!card) return;
  var sourceBody=source.querySelector('tbody');
  var rows=Array.from(sourceBody.querySelectorAll('tr.fbm-order-row'));
  var headerRow=source.querySelector('thead tr');
  function ensureCostHeader(table){{var head=table.querySelector('thead tr');if(!head) return;if(Array.from(head.children).some(function(th){{return th.dataset&&th.dataset.fbmShippingCost==='1';}})) return;var th=document.createElement('th');th.textContent='Shipping cost';th.dataset.fbmShippingCost='1';head.insertBefore(th,head.lastElementChild);}}
  function addCostCell(row,info){{if(row.querySelector('[data-fbm-shipping-cost="1"]')) return;var td=document.createElement('td');td.dataset.fbmShippingCost='1';if(info.shipping_cost_confirmed){{td.className='fbm-shipping-cost';try{{td.textContent=new Intl.NumberFormat('en-GB',{{style:'currency',currency:info.shipping_currency||'GBP'}}).format(info.shipping_cost);}}catch(e){{td.textContent=(info.shipping_currency||'GBP')+' '+Number(info.shipping_cost).toFixed(2);}}}}else{{td.className='fbm-shipping-cost-pending';td.textContent='Pending / unavailable';}}row.insertBefore(td,row.lastElementChild);}}
  ensureCostHeader(source);
  rows.forEach(function(row){{var info=data[row.dataset.orderId]||{{queue:'needs_dispatch'}};row.dataset.fbmQueue=info.queue;addCostCell(row,info);}});

  var dispatchedRows=rows.filter(function(row){{return row.dataset.fbmQueue==='dispatched';}});
  var activeRows=rows.filter(function(row){{return row.dataset.fbmQueue==='needs_dispatch';}});
  rows.filter(function(row){{return row.dataset.fbmQueue!=='needs_dispatch';}}).forEach(function(row){{row.remove();}});

  var title=card.querySelector('.card-header .fw-semibold');
  if(title) title.textContent='Needs dispatch';
  var selectedBadge=document.getElementById('selectedOrderCount');
  if(selectedBadge) selectedBadge.insertAdjacentHTML('afterend','<span class="text-muted small ms-2" id="bt38NeedsDispatchCount"></span>');
  var activeCount=document.getElementById('bt38NeedsDispatchCount');
  if(activeCount) activeCount.textContent=activeRows.length+' loaded orders requiring shipping action';

  if(dispatchedRows.length){{
    var history=document.createElement('div');history.className='card fbm-dispatch-history';
    var historyHeader=document.createElement('div');historyHeader.className='card-header d-flex justify-content-between align-items-center flex-wrap gap-2';
    var historyTitle=document.createElement('span');historyTitle.className='fw-semibold';historyTitle.textContent='Dispatched';
    var historyMeta=document.createElement('span');historyMeta.className='text-muted small';historyMeta.textContent=dispatchedRows.length+' loaded orders · tracking / dispatch recorded';
    historyHeader.appendChild(historyTitle);historyHeader.appendChild(historyMeta);
    var responsive=document.createElement('div');responsive.className='table-responsive';
    var historyTable=source.cloneNode(true);historyTable.removeAttribute('id');
    historyTable.querySelectorAll('[id]').forEach(function(node){{node.removeAttribute('id');}});
    var historyHead=historyTable.querySelector('thead tr');
    var historyBody=historyTable.querySelector('tbody');historyBody.innerHTML='';
    dispatchedRows.forEach(function(row){{var clone=row.cloneNode(true);clone.querySelectorAll('.fbm-order-checkbox,.fbm-shipping-options,.packlink-existing-status').forEach(function(node){{node.remove();}});historyBody.appendChild(clone);}});
    if(historyHead&&historyHead.children.length) historyHead.children[0].remove();
    Array.from(historyBody.children).forEach(function(row){{if(row.children.length) row.children[0].remove();}});
    if(historyHead&&historyHead.lastElementChild) historyHead.lastElementChild.remove();
    Array.from(historyBody.children).forEach(function(row){{if(row.lastElementChild) row.lastElementChild.remove();}});
    responsive.appendChild(historyTable);history.appendChild(historyHeader);history.appendChild(responsive);
    card.insertAdjacentElement('afterend',history);
  }}

  var selectAll=document.getElementById('selectAllOrders');
  if(selectAll) selectAll.checked=false;
  var selectedCount=document.getElementById('selectedOrderCount');if(selectedCount) selectedCount.textContent='0 selected';
  var readyButton=document.getElementById('readyToShipSelected');if(readyButton) readyButton.disabled=true;
}})();
</script>'''
    return html.replace(marker, block + marker, 1) if marker in html else html + block


def install_governed_fbm_dispatch_queue_alignment(app) -> None:
    """Wrap the existing governed FBM page with read-only queue presentation."""
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
        rows = (
            db.session.query(MarketplaceOrder)
            .filter(MarketplaceOrder.id.in_(order_ids))
            .all()
        )
        by_id = {row.id: row for row in rows}
        ordered_rows = [by_id[order_id] for order_id in order_ids if order_id in by_id]
        response.set_data(_inject(html, _presentation(ordered_rows)))
        return response

    app.view_functions[endpoint] = aligned_fbm_page
    app._bt38_fbm_dispatch_queue_alignment_installed = True
    app.logger.info("BT38 FBM dispatch queue alignment installed: original active workspace preserved, dispatched history separated, confirmed shipping spend reused")
