"""Align the notification bell to the existing exact committed-event path.

The bell is presentation only. It never queries Neon, marketplace APIs, carrier
APIs, or reconstructs order truth when opened. Exact committed events carry the
small scalar presentation fields already present on the changed canonical row.
The browser keeps a bounded copy of events it actually observed so a subsequent
notification-panel GET cannot lose an event merely because another Gunicorn
worker serves that request.
"""
from __future__ import annotations

from flask_login import login_required


_PRESENTATION_SCOPE_KEYS = (
    "platform",
    "marketplace_order_id",
    "status",
    "lifecycle_status",
    "quantity",
    "carrier",
    "tracking_number",
    "product_title",
    "fulfillment_type",
    "provider",
)


def _normalise(value) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _label_for_event(event: dict) -> str | None:
    values = " ".join(
        _normalise(event.get(key))
        for key in (
            "lifecycle_status",
            "status",
            "event_type",
            "source",
        )
        if event.get(key) not in (None, "")
    )
    ordered = (
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
        (("delivered",), "Delivered"),
        (("out_for_delivery",), "Out for delivery"),
        (("in_transit",), "In transit"),
        (("carrier_accepted", "picked_up", "collected", "accepted"), "Picked up"),
        (("marketplace_dispatch_confirmed", "label_assigned", "dispatched", "shipped", "partially_shipped"), "Dispatched"),
        (("pending", "unshipped", "confirmed", "order_received", "new_order", "order_committed"), "Sale"),
    )
    for tokens, label in ordered:
        if any(token in values for token in tokens):
            return label

    source = _normalise(event.get("source"))
    order_id = str(event.get("order_id") or event.get("marketplace_order_id") or "").strip()
    sku = str(event.get("seller_sku") or event.get("sku") or "").strip()
    if source in {"webhook_amazon", "webhook_ebay"} and order_id and sku:
        return "Sale"
    return None


def _platform_for_event(event: dict) -> str:
    explicit = str(event.get("platform") or "").strip()
    if explicit:
        return explicit
    source = _normalise(event.get("source"))
    if "amazon" in source:
        return "Amazon"
    if "ebay" in source:
        return "eBay"
    provider = str(event.get("provider") or "").strip()
    if provider:
        return provider
    carrier = str(event.get("carrier") or "").strip()
    if carrier:
        return carrier
    return "Marketplace"


def _event_to_bell_record(event: dict) -> dict | None:
    label = _label_for_event(event)
    if not label:
        return None

    revision = int(event.get("revision") or 0)
    order_id = str(event.get("order_id") or event.get("marketplace_order_id") or "").strip()
    sku = str(event.get("seller_sku") or event.get("sku") or "").strip()
    platform = _platform_for_event(event)
    product_title = str(event.get("product_title") or "").strip()
    quantity = event.get("quantity")
    carrier = str(event.get("carrier") or event.get("provider") or "").strip()
    tracking = str(event.get("tracking_number") or "").strip()
    subject = product_title or sku or order_id or "Order"
    title = f"{label} · {platform} · {subject}"

    details = []
    if order_id:
        details.append(f"Order {order_id}")
    if sku:
        details.append(f"SKU {sku}")
    if quantity not in (None, ""):
        details.append(f"Qty {quantity}")
    if carrier:
        details.append(f"Carrier {carrier}")
    if tracking:
        details.append(f"Tracking {tracking}")
    message = " · ".join(details) if details else title
    lifecycle = _normalise(event.get("lifecycle_status") or event.get("status"))

    return {
        "event_key": f"runtime:{revision}:{_normalise(label)}:{order_id}:{sku}",
        "id": f"runtime:{revision}",
        "log_type": "marketplace_sale" if label == "Sale" else "marketplace_lifecycle",
        "platform": platform,
        "title": title,
        "message": message,
        "order_id": order_id,
        "sku": sku,
        "quantity": quantity,
        "carrier": carrier,
        "tracking_number": tracking,
        "lifecycle_status": lifecycle,
        "status_label": label,
        "created_at": event.get("published_at"),
    }


def _patch_exact_scope() -> None:
    """Carry only already-loaded scalar fields through the existing event queue."""
    from fbm_models import FBMShipment
    from models import MarketplaceOrder
    from services import governed_exact_record_event_alignment as exact
    from services import governed_ui_event_signal as ui

    ui._SINGULAR_SCOPE_KEYS = tuple(dict.fromkeys(tuple(ui._SINGULAR_SCOPE_KEYS) + _PRESENTATION_SCOPE_KEYS))

    if getattr(exact, "_bt38_bell_projection_scope_patched", False):
        return

    original = exact._row_scope

    def projected_scope(row):
        scope = dict(original(row) or {})
        if isinstance(row, MarketplaceOrder):
            scalar = {
                "marketplace_order_id": getattr(row, "marketplace_order_id", None),
                "status": getattr(row, "status", None),
                "lifecycle_status": getattr(row, "status", None),
                "quantity": getattr(row, "quantity", None),
                "carrier": getattr(row, "carrier", None),
                "tracking_number": getattr(row, "tracking_number", None),
                "product_title": getattr(row, "product_title", None),
                "fulfillment_type": getattr(row, "fulfillment_type", None),
                "platform": getattr(row, "platform", None) or getattr(row, "marketplace", None),
            }
            scope.update({key: value for key, value in scalar.items() if value not in (None, "")})
        elif isinstance(row, FBMShipment):
            scalar = {
                "status": getattr(row, "status", None),
                "lifecycle_status": getattr(row, "status", None),
                "carrier": getattr(row, "carrier", None),
                "tracking_number": getattr(row, "tracking_number", None),
                "provider": getattr(row, "provider", None),
            }
            scope.update({key: value for key, value in scalar.items() if value not in (None, "")})
        return scope

    exact._row_scope = projected_scope
    exact._bt38_bell_projection_scope_patched = True


def _browser_event_cache_script() -> str:
    """Keep only exact live commercial events already observed by this browser."""
    return r'''
<script id="bt38ExactBellBrowserCache">
(function(){
  if(window.bt38ExactBellBrowserCacheInstalled)return;
  window.bt38ExactBellBrowserCacheInstalled=true;
  var cacheKey='bt38.notifications.exactEventRecords.v1';

  function norm(value){return String(value||'').trim().toLowerCase().replace(/[- ]/g,'_');}
  function labelFor(detail){
    var value=[detail.lifecycle_status,detail.status,detail.event_type,detail.source].map(norm).join(' ');
    var rules=[
      [['return_fulfillment_completed','return_closed','returned'],'Returned'],
      [['return_requested','return_fulfillment_initiated'],'Return requested'],
      [['refund_requested'],'Refund requested'],
      [['refunded','refund_completed','refund_issued'],'Refunded'],
      [['cancellation_requested','cancel_requested'],'Cancellation requested'],
      [['cancelled','canceled'],'Cancelled'],
      [['replacement_requested'],'Replacement requested'],
      [['replacement','replaced'],'Replacement'],
      [['chargeback'],'Chargeback'],[['dispute'],'Dispute'],[['case_open','case_opened'],'Issue / case'],
      [['delivered'],'Delivered'],[['out_for_delivery'],'Out for delivery'],[['in_transit'],'In transit'],
      [['carrier_accepted','picked_up','collected','accepted'],'Picked up'],
      [['marketplace_dispatch_confirmed','label_assigned','dispatched','shipped','partially_shipped'],'Dispatched'],
      [['pending','unshipped','confirmed','order_received','new_order','order_committed'],'Sale']
    ];
    for(var i=0;i<rules.length;i++)if(rules[i][0].some(function(token){return value.indexOf(token)>=0;}))return rules[i][1];
    var source=norm(detail.source),orderId=String(detail.order_id||detail.marketplace_order_id||'').trim();
    var sku=String(detail.seller_sku||detail.sku||'').trim();
    if((source==='webhook_amazon'||source==='webhook_ebay')&&orderId&&sku)return 'Sale';
    return '';
  }
  function platformFor(detail){
    var explicit=String(detail.platform||'').trim();if(explicit)return explicit;
    var source=norm(detail.source);if(source.indexOf('amazon')>=0)return 'Amazon';if(source.indexOf('ebay')>=0)return 'eBay';
    var provider=String(detail.provider||'').trim();if(provider)return provider;
    var carrier=String(detail.carrier||'').trim();if(carrier)return carrier;
    return 'Marketplace';
  }
  function read(){try{var value=JSON.parse(localStorage.getItem(cacheKey)||'[]');return Array.isArray(value)?value:[];}catch(_){return [];}}
  function write(rows){try{localStorage.setItem(cacheKey,JSON.stringify(rows.slice(0,50)));}catch(_){}}
  function recordFor(detail){
    if(!detail||typeof detail!=='object')return null;
    var label=labelFor(detail);if(!label)return null;
    var revision=Number(detail.revision||0),orderId=String(detail.order_id||detail.marketplace_order_id||'').trim();
    var sku=String(detail.seller_sku||detail.sku||'').trim(),platform=platformFor(detail);
    var productTitle=String(detail.product_title||'').trim(),subject=productTitle||sku||orderId||'Order';
    var carrier=String(detail.carrier||detail.provider||'').trim(),tracking=String(detail.tracking_number||'').trim();
    var quantity=detail.quantity,parts=[];
    if(orderId)parts.push('Order '+orderId);if(sku)parts.push('SKU '+sku);if(quantity!==undefined&&quantity!==null&&quantity!=='')parts.push('Qty '+quantity);
    if(carrier)parts.push('Carrier '+carrier);if(tracking)parts.push('Tracking '+tracking);
    var title=label+' · '+platform+' · '+subject,message=parts.length?parts.join(' · '):title;
    return {event_key:'runtime:'+revision+':'+norm(label)+':'+orderId+':'+sku,id:'runtime:'+revision,
      log_type:label==='Sale'?'marketplace_sale':'marketplace_lifecycle',platform:platform,title:title,message:message,
      order_id:orderId,sku:sku,quantity:quantity,carrier:carrier,tracking_number:tracking,
      lifecycle_status:norm(detail.lifecycle_status||detail.status),status_label:label,
      created_at:detail.published_at||new Date().toISOString()};
  }
  function store(detail){var record=recordFor(detail);if(!record)return;var rows=read().filter(function(row){return row&&row.event_key!==record.event_key;});rows.unshift(record);write(rows);}
  window.addEventListener('bt38-marketplace-event',function(event){store(event&&event.detail||{});});

  var previousFetch=window.fetch.bind(window);
  window.fetch=async function(input,init){
    var response=await previousFetch(input,init),url=typeof input==='string'?input:(input&&input.url)||'';
    var method=String(init&&init.method||'GET').toUpperCase();
    if(method!=='GET'||url.indexOf('/governed/ui/notifications')!==0||!response.ok)return response;
    try{
      var payload=await response.clone().json();if(!payload||payload.success!==true)return response;
      var combined=read().concat(Array.isArray(payload.records)?payload.records:[]),seen=new Set(),unique=[];
      combined.sort(function(a,b){return Date.parse(b&&b.created_at||'')-Date.parse(a&&a.created_at||'');});
      combined.forEach(function(row){var key=String(row&&row.event_key||'').trim();if(!key||seen.has(key))return;seen.add(key);unique.push(row);});
      payload.records=unique.slice(0,50);payload.latest_event_at=payload.records.length?payload.records[0].created_at:null;
      var headers=new Headers(response.headers);headers.set('Content-Type','application/json');
      return new Response(JSON.stringify(payload),{status:response.status,statusText:response.statusText,headers:headers});
    }catch(_){return response;}
  };
})();
</script>
'''


def _inject_browser_cache(response):
    if response.status_code != 200 or "text/html" not in str(response.content_type or "").lower():
        return response
    html = response.get_data(as_text=True)
    if 'id="bt38NotificationBell"' not in html or 'id="bt38ExactBellBrowserCache"' in html:
        return response
    script = _browser_event_cache_script()
    marker = "</body>"
    response.set_data(html.replace(marker, script + marker, 1) if marker in html else html + script)
    return response


def install_governed_bell_event_projection_alignment(app) -> None:
    """Install one final bell projection over the existing event/session path."""
    from services import governed_fbm_ready_landing_alignment as ready

    _patch_exact_scope()
    ready._event_to_bell_record = _event_to_bell_record

    endpoint = "governed.governed_ui_notifications"
    if endpoint in app.view_functions:
        app.view_functions[endpoint] = login_required(ready._event_only_bell_reader)

    if not getattr(app, "_bt38_exact_bell_browser_cache_installed", False):
        app.after_request(_inject_browser_cache)
        app._bt38_exact_bell_browser_cache_installed = True

    app.logger.info(
        "BT38 bell aligned: exact committed scalar projection, zero DB/API bell reads, bounded browser-observed history, no polling"
    )
