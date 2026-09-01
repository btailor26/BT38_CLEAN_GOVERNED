"""Present the existing FBM workspace as actionable and dispatched queues.

This alignment is presentation/read-only. It reuses the registered governed FBM
page, MarketplaceOrder/FBMShipment truth, and confirmed ShippingSpendLedger
records. It does not create another fulfilment path and never writes order,
shipment, inventory, marketplace, or spend state.
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
        spend = spend_by_shipment.get(int(shipment.id)) if shipment and shipment.id else None
        payload[str(row.id)] = {
            "queue": "dispatched" if dispatched else "needs_dispatch",
            "shipping_cost": float(spend.amount) if spend is not None else None,
            "shipping_currency": str(spend.currency or "GBP").upper() if spend is not None else None,
            "shipping_cost_confirmed": spend is not None,
        }
    return payload


def _inject(html: str, payload: dict[str, dict]) -> str:
    data = json.dumps(payload, separators=(",", ":"), sort_keys=True).replace("</", "<\\/")
    marker = "</body>"
    block = f'''<style id="bt38FbmDispatchQueueAlignment">
.fbm-queue-section{{margin-top:.8rem}}.fbm-queue-head{{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:.65rem .8rem;border:1px solid #e1e5eb;border-bottom:0;border-radius:9px 9px 0 0;background:#f8fafc}}.fbm-queue-head strong{{font-size:.92rem}}.fbm-queue-head span{{font-size:.72rem;color:#667085}}.fbm-queue-section .table-responsive{{border:1px solid #e1e5eb;border-radius:0 0 9px 9px}}.fbm-shipping-cost{{white-space:nowrap;font-weight:650}}.fbm-shipping-cost-pending{{font-size:.72rem;color:#667085;white-space:nowrap}}.fbm-dispatched-queue .fbm-order-checkbox{{display:none}}.fbm-dispatched-queue th:first-child{{color:transparent}}.fbm-dispatched-queue .fbm-action-cell .fbm-shipping-options{{display:none}}
</style>
<script id="bt38FbmDispatchQueueAlignmentData" type="application/json">{data}</script>
<script id="bt38FbmDispatchQueueAlignmentScript">
(function(){{
  var source=document.querySelector('.fbm-orders-table');
  var dataNode=document.getElementById('bt38FbmDispatchQueueAlignmentData');
  if(!source||!dataNode) return;
  var data={{}}; try{{data=JSON.parse(dataNode.textContent||'{{}}')}}catch(e){{return;}}
  var card=source.closest('.card'); if(!card) return;
  var rows=Array.from(source.querySelectorAll('tbody tr.fbm-order-row'));
  var headerRow=source.querySelector('thead tr');
  if(headerRow&&!Array.from(headerRow.children).some(function(th){{return th.dataset&&th.dataset.fbmShippingCost==='1';}})){{var th=document.createElement('th');th.textContent='Shipping cost';th.dataset.fbmShippingCost='1';headerRow.insertBefore(th,headerRow.lastElementChild);}}
  rows.forEach(function(row){{var info=data[row.dataset.orderId]||{{}};var td=document.createElement('td');td.dataset.fbmShippingCost='1';if(info.shipping_cost_confirmed){{td.className='fbm-shipping-cost';try{{td.textContent=new Intl.NumberFormat('en-GB',{{style:'currency',currency:info.shipping_currency||'GBP'}}).format(info.shipping_cost);}}catch(e){{td.textContent=(info.shipping_currency||'GBP')+' '+Number(info.shipping_cost).toFixed(2);}}}}else{{td.className='fbm-shipping-cost-pending';td.textContent='Pending / unavailable';}}row.insertBefore(td,row.lastElementChild);row.dataset.fbmQueue=info.queue||'needs_dispatch';}});
  function section(title,copy,klass,queue){{var wrap=document.createElement('section');wrap.className='fbm-queue-section '+klass;var head=document.createElement('div');head.className='fbm-queue-head';var strong=document.createElement('strong');strong.textContent=title;var meta=document.createElement('span');var matching=rows.filter(function(r){{return r.dataset.fbmQueue===queue;}});meta.textContent=matching.length+' orders · '+copy;head.appendChild(strong);head.appendChild(meta);var responsive=document.createElement('div');responsive.className='table-responsive';var table=source.cloneNode(true);table.removeAttribute('id');var body=table.querySelector('tbody');body.innerHTML='';matching.forEach(function(r){{body.appendChild(r);}});responsive.appendChild(table);wrap.appendChild(head);wrap.appendChild(responsive);return wrap;}}
  var needs=section('Needs dispatch','shipping action required','fbm-needs-dispatch-queue','needs_dispatch');
  var done=section('Dispatched','tracking / dispatch recorded','fbm-dispatched-queue','dispatched');
  card.parentNode.insertBefore(needs,card);card.parentNode.insertBefore(done,card);card.remove();
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
    app.logger.info("BT38 FBM dispatch queue alignment installed: needs-dispatch/dispatched split with confirmed shipping spend")
