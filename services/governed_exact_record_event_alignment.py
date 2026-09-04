"""Final exact-record event/session alignment.

This replaces the old generic commit wake with one exact affected-record event
and keeps the existing in-memory SSE/BroadcastChannel transport. It also
neutralises broad browser rebuild/read behaviour that had drifted into the
shared presentation controller.

Contract:
- zero polling / zero idle DB work
- no page/table/workspace rebuild after a committed event
- one committed event carries exact affected identities
- same-page and cross-page consumers preserve the current browser session
- bell remains an in-memory observer only
"""
from __future__ import annotations

import json

from flask import Response, has_request_context, request
from sqlalchemy import event
from sqlalchemy.orm import Session

from services import governed_ui_event_signal as ui


_INSTALLED = False
_EXPLICIT_PUBLISH_PATHS = {
    "/governed/webhooks/amazon",
    "/governed/webhooks/ebay",
    "/governed/product-linking/link-listing-to-warehouse",
}


def _value(row, *names):
    for name in names:
        value = getattr(row, name, None)
        if value not in (None, ""):
            return value
    return None


def _row_scope(row) -> dict:
    from fbm_models import FBMShipment
    from models import MarketplaceListing, MarketplaceOrder

    scope = {}
    if isinstance(row, MarketplaceListing):
        listing_id = _value(row, "id")
        stock_id = _value(row, "warehouse_stock_id", "inventory_item_id")
        group_id = _value(row, "master_product_group_id", "group_id")
        if listing_id is not None:
            scope["listing_id"] = listing_id
            scope["affected_listing_ids"] = [listing_id]
        if stock_id is not None:
            scope["warehouse_stock_id"] = stock_id
            scope["affected_warehouse_stock_ids"] = [stock_id]
        if group_id is not None:
            scope["group_id"] = group_id
            scope["affected_group_ids"] = [group_id]
        scope["seller_sku"] = _value(row, "seller_sku", "sku")
        scope["store_id"] = _value(row, "store_id")
        scope["event_type"] = "listing_committed"
    elif isinstance(row, MarketplaceOrder):
        scope["order_id"] = _value(row, "order_id", "marketplace_order_id")
        scope["seller_sku"] = _value(row, "seller_sku", "sku")
        scope["store_id"] = _value(row, "store_id")
        stock_id = _value(row, "warehouse_stock_id")
        if stock_id is not None:
            scope["warehouse_stock_id"] = stock_id
            scope["affected_warehouse_stock_ids"] = [stock_id]
        scope["event_type"] = "order_committed"
    elif isinstance(row, FBMShipment):
        scope["order_id"] = _value(row, "order_id", "marketplace_order_id")
        scope["store_id"] = _value(row, "store_id")
        scope["event_type"] = "shipment_committed"
    return {key: value for key, value in scope.items() if value not in (None, "")}


def _exact_before_flush(session_obj, flush_context, instances):
    if session_obj.info.get("_bt38_exact_ui_scope"):
        return
    if has_request_context() and (request.path.rstrip("/") or "/") in _EXPLICIT_PUBLISH_PATHS:
        return

    from fbm_models import FBMShipment
    from models import MarketplaceListing, MarketplaceOrder

    canonical = (MarketplaceListing, MarketplaceOrder, FBMShipment)
    scope = {}
    rows = list(session_obj.new) + list(session_obj.dirty) + list(session_obj.deleted)
    for row in rows:
        if not isinstance(row, canonical):
            continue
        if row in session_obj.dirty and not session_obj.is_modified(row, include_collections=False):
            continue
        ui._merge_scope(scope, _row_scope(row))

    if scope:
        session_obj.info["_bt38_exact_ui_scope"] = scope


def _exact_after_commit(session_obj):
    scope = session_obj.info.pop("_bt38_exact_ui_scope", None)
    if scope:
        ui.publish_governed_ui_event(source="committed_marketplace_state", scope=scope)


def _exact_after_rollback(session_obj):
    session_obj.info.pop("_bt38_exact_ui_scope", None)


def _event_stream_response():
    """Existing in-memory transport, now carrying the exact event payload."""
    from flask import session

    if not session.get("_user_id"):
        return Response(status=401)

    with ui._condition:
        initial_revision = int(ui._revision)

    def _stream():
        seen_revision = initial_revision
        yield "retry: 3000\n\n"
        while True:
            with ui._condition:
                if int(ui._revision) == seen_revision:
                    ui._condition.wait(timeout=25.0)
                events = ui._events_after(seen_revision)
                current_revision = int(ui._revision)

            if events:
                for committed in events:
                    seen_revision = int(committed.get("revision") or seen_revision)
                    yield "event: marketplace\n" + "data: " + json.dumps(committed, separators=(",", ":")) + "\n\n"
            elif current_revision != seen_revision:
                seen_revision = current_revision
            else:
                yield ": keepalive\n\n"

    response = Response(_stream(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache, no-transform"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    return response


def _align_base_event_payload(html: str) -> str:
    html = html.replace(
        'dispatchLiveBellSignal({\n                        source: "browser_leader",\n                        sequence: event.data.sequence || ""\n                    });',
        'dispatchLiveBellSignal(Object.assign({}, event.data.event || {}, {source: "browser_leader", sequence: String((event.data.event || {}).revision || event.data.sequence || "")}));',
    )
    html = html.replace(
        '// Wake this tab.\n                            dispatchLiveBellSignal({\n                                source: "server_commit",\n                                sequence: event.data || ""\n                            });\n\n                            // Wake every other BT38 tab without another\n                            // server connection.\n                            try {\n                                liveChannel.postMessage({\n                                    type: "marketplace",\n                                    sequence: event.data || ""\n                                });',
        '// Exact committed-record handoff. No page rebuild or follow-up discovery read.\n                            let committedEvent = {};\n                            try { committedEvent = JSON.parse(event.data || "{}"); } catch (error) { committedEvent = {}; }\n                            committedEvent.source = "server_commit";\n                            committedEvent.sequence = String(committedEvent.revision || "");\n                            dispatchLiveBellSignal(committedEvent);\n\n                            // Wake every other BT38 tab without another server connection.\n                            try {\n                                liveChannel.postMessage({\n                                    type: "marketplace",\n                                    event: committedEvent,\n                                    sequence: committedEvent.sequence\n                                });',
    )
    return html


def _replace_function(source: str, start_marker: str, end_marker: str, replacement: str) -> str:
    start = source.find(start_marker)
    end = source.find(end_marker, start + len(start_marker)) if start >= 0 else -1
    if start < 0 or end < 0:
        return source
    return source[:start] + replacement + "\n\n  " + source[end:]


def _align_shared_controller(source: str) -> str:
    source = source.replace("let pendingWhileHidden = false;", "let pendingWhileHidden = null;")
    source = _replace_function(
        source,
        "async function refreshProductLinkingSilently() {",
        "function pageOwnsCommittedRefresh() {",
        '''async function refreshProductLinkingSilently(detail) {
    if (productLinkingRefreshRunning) return false;
    if (typeof window.bt38RefreshProductLinkingRecord !== 'function') return false;
    const exactSku = String(detail?.seller_sku || '').trim();
    if (!exactSku) return false;

    productLinkingRefreshRunning = true;
    try {
      await window.bt38RefreshProductLinkingRecord({listingSku: exactSku, warehouseSku: exactSku});
      return true;
    } catch (error) {
      console.warn('[BT38 UI] exact Product Linking record refresh failed', error);
      return false;
    } finally {
      productLinkingRefreshRunning = false;
    }
  }''',
    )
    source = _replace_function(
        source,
        "async function refreshCurrentPage() {",
        "window.addEventListener('bt38-marketplace-event'",
        '''async function refreshCurrentPage(detail) {
    if (pageOwnsCommittedRefresh()) return;
    if (document.visibilityState === 'hidden') {
      pendingWhileHidden = detail || null;
      return;
    }
    if (document.querySelector('[data-bt38-page="productLinking"]')) {
      await refreshProductLinkingSilently(detail || {});
    }
  }''',
    )
    source = source.replace("void refreshCurrentPage();\n  });\n\n  document.addEventListener('visibilitychange'", "void refreshCurrentPage(event.detail || {});\n  });\n\n  document.addEventListener('visibilitychange'", 1)
    source = source.replace("if (document.visibilityState !== 'visible' || !pendingWhileHidden) return;\n    pendingWhileHidden = false;\n    void refreshCurrentPage();", "if (document.visibilityState !== 'visible' || !pendingWhileHidden) return;\n    const committed = pendingWhileHidden;\n    pendingWhileHidden = null;\n    void refreshCurrentPage(committed);")
    source = _replace_function(
        source,
        "async function readDashboardActionCount() {",
        "async function refreshAssistant(options) {",
        '''async function readDashboardActionCount() {
    const local = currentPageDashboardCount();
    if (Number.isFinite(local)) return local;
    const cached = Number(window.sessionStorage.getItem(cacheKey));
    return Number.isFinite(cached) ? cached : null;
  }''',
    )
    return source


def install_governed_exact_record_event_alignment(app) -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    for identifier, fn in (
        ("before_flush", ui._bt38_existing_ui_signal_before_flush),
        ("after_commit", ui._bt38_existing_ui_signal_after_commit),
        ("after_rollback", ui._bt38_existing_ui_signal_after_rollback),
    ):
        try:
            event.remove(Session, identifier, fn)
        except Exception:
            pass

    event.listen(Session, "before_flush", _exact_before_flush)
    event.listen(Session, "after_commit", _exact_after_commit)
    event.listen(Session, "after_rollback", _exact_after_rollback)

    if "governed_ui_event_stream" in app.view_functions:
        app.view_functions["governed_ui_event_stream"] = _event_stream_response

    @app.after_request
    def _bt38_exact_record_browser_alignment(response):
        path = request.path.rstrip("/") or "/"
        content_type = str(response.content_type or "").lower()
        if response.status_code != 200:
            return response
        if path == "/static/js/bt38-live-page-refresh.js" and "javascript" in content_type:
            if response.direct_passthrough:
                response.direct_passthrough = False
            response.set_data(_align_shared_controller(response.get_data(as_text=True)))
            return response
        if "text/html" in content_type:
            body = response.get_data(as_text=True)
            if 'id="bt38NotificationBell"' in body:
                response.set_data(_align_base_event_payload(body))
        return response

    _INSTALLED = True
    app.logger.info("BT38 exact-record event/session alignment installed: zero polling, zero rebuild, exact handoff")
