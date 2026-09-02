"""Align the existing FBM workspace to the simple human workflow.

Presentation/read-only only. The registered FBM page remains the one workspace and
existing order table. Existing FBA and MCF pages remain their truth surfaces; FBM
only links to them as shortcuts. Cofi is the user-facing guide. No
marketplace/provider/DB write is introduced.
"""
from __future__ import annotations

import json
import re

from flask import make_response
from flask_login import login_required
from sqlalchemy import or_

from extensions import db
from fbm_models import FBMShipment
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


def _sds_committed(shipment) -> bool:
    """Conservatively expose SDS only when persisted journey/commit evidence exists.

    Selecting SDS alone is not enough. Current SDS label rendering is deliberately
    read-only, so until a dedicated label-confirmation fact exists we require a
    stronger persisted shipment fact rather than inventing a print/driver state.
    """
    if shipment is None or str(getattr(shipment, "provider", "") or "").strip().lower() != "sds":
        return False
    purchase_status = str(getattr(shipment, "purchase_status", "") or "").strip().lower()
    return bool(
        getattr(shipment, "label_purchased_at", None)
        or getattr(shipment, "carrier_accepted_at", None)
        or getattr(shipment, "first_movement_at", None)
        or getattr(shipment, "delivered_at", None)
        or getattr(shipment, "tracking_number", None)
        or purchase_status in {"confirmed", "purchased", "committed"}
    )


def _queue_for(row: MarketplaceOrder, shipment) -> str:
    status = str(getattr(row, "status", "") or "").strip().lower()
    reason = _status_reason(status)
    if status in _CANCELLED_STATUSES or status.startswith("cancel"):
        return "excluded"
    if reason:
        return reason
    if _sds_committed(shipment):
        return "sds"
    dispatched = bool(
        getattr(row, "tracking_number", None)
        or getattr(row, "shipped_at", None)
        or (shipment and getattr(shipment, "tracking_number", None))
        or (shipment and getattr(shipment, "carrier_accepted_at", None))
        or (shipment and getattr(shipment, "first_movement_at", None))
        or (shipment and getattr(shipment, "delivered_at", None))
    )
    return "dispatched" if dispatched else "ready_dispatch"


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
        queue = _queue_for(row, shipment)
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
    """Use existing persisted health truth for primary counts, never loaded DOM rows."""
    from services import governed_fbm_page_alignment as page_alignment

    try:
        health = page_alignment._health_summary()
    except Exception:
        health = {}

    try:
        sds_rows = (
            db.session.query(FBMShipment.store_id, FBMShipment.marketplace_order_id)
            .filter(db.func.lower(db.func.coalesce(FBMShipment.provider, "")) == "sds")
            .filter(or_(
                FBMShipment.label_purchased_at.isnot(None),
                FBMShipment.carrier_accepted_at.isnot(None),
                FBMShipment.first_movement_at.isnot(None),
                FBMShipment.delivered_at.isnot(None),
                FBMShipment.tracking_number.isnot(None),
                db.func.lower(db.func.coalesce(FBMShipment.purchase_status, "")).in_(("confirmed", "purchased", "committed")),
            ))
            .all()
        )
        sds_count = len({(int(store_id), str(order_id)) for store_id, order_id in sds_rows if store_id is not None and order_id})
    except Exception:
        sds_count = 0

    return {
        "ready_dispatch": int(health.get("dispatch_due", health.get("ready", 0)) or 0),
        "dispatched": int(health.get("dispatched", 0) or 0),
        "sds": sds_count,
        "replacements": int(health.get("replacements", 0) or 0),
        "refunds": int(health.get("refund_issues", 0) or 0),
    }


def _align_cofi_ui(html: str) -> str:
    """Cofi owns user-facing guidance; Sentinel and operational authority stay untouched."""
    replacements = {
        'alt="BT38 shipping guide"': 'alt="Cofi"',
        "BT38 will keep the queue visible.": "Cofi will keep the queue visible.",
        "Everything that needs a shipping action is clear for this period.": "Everything that needs a shipping action is clear. Cofi will keep watching the work queue.",
        "Work through the important shipping actions first.": "Cofi has put the important shipping actions first.",
    }
    for old, new in replacements.items():
        html = html.replace(old, new)
    return html


def _inject(html: str, payload: dict[str, dict], counts: dict[str, int]) -> str:
    html = _align_cofi_ui(html)
    data = json.dumps(payload, separators=(",", ":"), sort_keys=True).replace("</", "<\\/")
    count_data = json.dumps(counts, separators=(",", ":"), sort_keys=True).replace("</", "<\\/")
    marker = "</body>"
    block = f'''<style id="bt38FbmLifecycleTabsAlignment">
.fbm-lifecycle-tabs{{display:flex;gap:.35rem;overflow-x:auto;padding:.45rem .5rem;border-bottom:1px solid #dee2e6;background:var(--bs-body-bg,#fff);scrollbar-width:thin}}.fbm-lifecycle-tab{{white-space:nowrap;border:1px solid #d0d5dd;background:transparent;border-radius:.375rem;padding:.38rem .62rem;font-size:.78rem;font-weight:650;color:inherit;text-decoration:none}}.fbm-lifecycle-tab:hover{{color:inherit}}.fbm-lifecycle-tab.active{{background:#212529;color:#fff;border-color:#212529}}.fbm-lifecycle-tab .badge{{margin-left:.3rem;font-size:.62rem}}.fbm-shipping-cost{{white-space:nowrap;font-weight:650}}.fbm-shipping-cost-pending{{font-size:.72rem;color:#667085;white-space:nowrap}}.fbm-tab-empty{{padding:1.2rem;text-align:center;color:#667085;font-size:.82rem}}@media(max-width:767.98px){{.fbm-lifecycle-tabs{{padding:.4rem}}.fbm-lifecycle-tab{{padding:.34rem .5rem}}}}
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

  function ensureCostHeader(){{var head=table.querySelector('thead tr');if(!head) return;if(head.querySelector('[data-fbm-shipping-cost="1"]')) return;var th=document.createElement('th');th.textContent='Shipping cost';th.dataset.fbmShippingCost='1';head.insertBefore(th,head.lastElementChild);}}
  function addCostCell(row,info){{if(row.querySelector('[data-fbm-shipping-cost="1"]')) return;var td=document.createElement('td');td.dataset.fbmShippingCost='1';if(info.shipping_cost_confirmed){{td.className='fbm-shipping-cost';try{{td.textContent=new Intl.NumberFormat('en-GB',{{style:'currency',currency:info.shipping_currency||'GBP'}}).format(info.shipping_cost);}}catch(e){{td.textContent=(info.shipping_currency||'GBP')+' '+Number(info.shipping_cost).toFixed(2);}}}}else{{td.className='fbm-shipping-cost-pending';td.textContent='Pending / unavailable';}}row.insertBefore(td,row.lastElementChild);}}
  ensureCostHeader();
  rows.forEach(function(row){{var info=data[row.dataset.orderId]||{{queue:'ready_dispatch'}};row.dataset.fbmQueue=info.queue;addCostCell(row,info);}});

  var filters=[['ready_dispatch','Ready to dispatch'],['dispatched','Dispatched']];
  var trailing=[['sds','SDS'],['replacements','Replacements'],['refunds','Refunds']];
  var tabBar=document.createElement('div');tabBar.className='fbm-lifecycle-tabs';tabBar.setAttribute('role','tablist');tabBar.setAttribute('aria-label','FBM workflow');
  function addButton(def){{var button=document.createElement('button');button.type='button';button.className='fbm-lifecycle-tab';button.dataset.fbmTab=def[0];button.setAttribute('role','tab');button.innerHTML=def[1]+' <span class="badge bg-light text-dark border">'+Number(counts[def[0]]||0)+'</span>';tabBar.appendChild(button);}}
  filters.forEach(addButton);
  [['FBA','/governed/amazon-fba-stock'],['MCF','/orders-mcf']].forEach(function(def){{var link=document.createElement('a');link.className='fbm-lifecycle-tab';link.href=def[1];link.textContent=def[0];link.title=def[0]+' truth';tabBar.appendChild(link);}});
  trailing.forEach(addButton);
  var header=card.querySelector('.card-header');if(header) header.insertAdjacentElement('afterend',tabBar);else card.insertBefore(tabBar,card.firstChild);
  var title=card.querySelector('.card-header .fw-semibold');
  var actionArea=document.getElementById('readyToShipSelected');
  var selectAll=document.getElementById('selectAllOrders');
  var selectedBadge=document.getElementById('selectedOrderCount');
  var empty=document.createElement('div');empty.className='fbm-tab-empty d-none';empty.textContent='No orders from the current loaded window are in this section. Use the persisted FBM search to find older orders.';table.closest('.table-responsive').insertAdjacentElement('afterend',empty);
  var allFilters=filters.concat(trailing);

  function resetSelection(){{rows.forEach(function(row){{var cb=row.querySelector('.fbm-order-checkbox');if(cb) cb.checked=false;}});if(selectAll) selectAll.checked=false;if(selectedBadge) selectedBadge.textContent='0 selected';if(actionArea) actionArea.disabled=true;}}
  function showTab(name){{resetSelection();var visible=0;rows.forEach(function(row){{var show=row.dataset.fbmQueue===name;row.classList.toggle('d-none',!show);if(show) visible++;}});Array.from(tabBar.querySelectorAll('[data-fbm-tab]')).forEach(function(btn){{var active=btn.dataset.fbmTab===name;btn.classList.toggle('active',active);btn.setAttribute('aria-selected',active?'true':'false');}});var match=allFilters.filter(function(def){{return def[0]===name;}})[0];if(title&&match) title.textContent=match[1];empty.classList.toggle('d-none',visible!==0);var actionable=name==='ready_dispatch';if(actionArea){{actionArea.classList.toggle('d-none',!actionable);var label=actionArea.childNodes[actionArea.childNodes.length-1];if(label&&label.nodeType===3) label.nodeValue=' Ready to dispatch';}}if(selectAll) selectAll.disabled=!actionable;rows.forEach(function(row){{var cb=row.querySelector('.fbm-order-checkbox');if(cb) cb.closest('td').classList.toggle('invisible',!actionable);var option=row.querySelector('.fbm-shipping-options');if(option) option.classList.toggle('d-none',!actionable);}});}}
  tabBar.addEventListener('click',function(event){{var btn=event.target.closest('[data-fbm-tab]');if(btn) showTab(btn.dataset.fbmTab);}});
  showTab('ready_dispatch');
}})();
</script>'''
    return html.replace(marker, block + marker, 1) if marker in html else html + block


def install_governed_fbm_dispatch_queue_alignment(app) -> None:
    """Wrap the existing governed FBM page with Cofi-led read-only workflow tabs."""
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
        rows: list[MarketplaceOrder] = []
        if order_ids:
            found = db.session.query(MarketplaceOrder).filter(MarketplaceOrder.id.in_(order_ids)).all()
            by_id = {row.id: row for row in found}
            rows = [by_id[order_id] for order_id in order_ids if order_id in by_id]
        response.set_data(_inject(html, _presentation(rows), _authoritative_counts()))
        return response

    app.view_functions[endpoint] = aligned_fbm_page
    app._bt38_fbm_dispatch_queue_alignment_installed = True
    app.logger.info("BT38 FBM workflow aligned: Cofi UI; persisted counts; Ready/Dispatched + FBA/MCF/SDS/Replacements/Refunds; no parallel workflow")
