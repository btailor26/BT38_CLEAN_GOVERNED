"""Event-driven DB -> UI handoff for governed BT38 pages.

Contract:
- publish only after a committed governed change
- preserve every unseen committed revision in a bounded in-memory queue
- carry exact affected listing / Warehouse / group identities
- no browser long-poll, Neon polling, broad scans, or full-page reloads
- the global bell pulls sales only when explicitly opened
"""
from __future__ import annotations

import threading
from collections import deque
from datetime import datetime

from flask import Response, g, has_request_context, jsonify, request, session
from sqlalchemy import event
from sqlalchemy.orm import Session
from app import app


_condition = threading.Condition()
_revision = 0
_events = deque(maxlen=256)

# The previous 25-second authenticated browser waiter could occupy every
# Gunicorn thread and hold read transactions open. Keep event publication for
# server-side governance, but do not install a browser waiter while the bell is
# the explicit sales-only shortcut.
LIVE_BROWSER_EVENT_WAITER_ENABLED = False

_LIVE_UI_PATHS = {
    "/warehouse",
    "/product-linking",
    "/amazon-fba-stock",
    "/listings",
    "/orders-mcf",
}

_WEBHOOK_PATHS = {
    "/governed/webhooks/amazon": "amazon",
    "/governed/webhooks/ebay": "ebay",
}

_RELATIONSHIP_EVENT_PATHS = {
    "/governed/product-linking/link-listing-to-warehouse": (
        "product_linking_link"
    ),
}

_SINGULAR_SCOPE_KEYS = (
    "event_type",
    "seller_sku",
    "listing_id",
    "order_id",
    "warehouse_stock_id",
    "group_id",
    "store_id",
)

_ARRAY_SCOPE_KEYS = (
    "affected_listing_ids",
    "affected_warehouse_stock_ids",
    "affected_group_ids",
)

_PAGE_SIZES = {15, 25, 50, 100}


def _requested_page_size(default: int = 15) -> int:
    try:
        value = int(request.args.get("per_page") or default)
    except Exception:
        value = default
    return value if value in _PAGE_SIZES else default


def _install_fba_paging_alignment() -> None:
    try:
        from flask_sqlalchemy.query import Query
    except Exception:
        return

    current = Query.paginate
    if getattr(current, "_bt38_fba_paging_aligned", False):
        return

    def aligned_paginate(self, *args, **kwargs):
        if (
            has_request_context()
            and (request.path.rstrip("/") or "/") == "/amazon-fba-stock"
        ):
            kwargs["per_page"] = _requested_page_size(15)
        return current(self, *args, **kwargs)

    aligned_paginate._bt38_fba_paging_aligned = True
    Query.paginate = aligned_paginate


_install_fba_paging_alignment()


def _normalise_ids(values):
    result = []
    seen = set()
    for value in list(values or []):
        if value in (None, ""):
            continue
        try:
            normalised = int(value)
        except (TypeError, ValueError):
            normalised = str(value)
        key = str(normalised)
        if key not in seen:
            seen.add(key)
            result.append(normalised)
    return result


def _merge_scope(target: dict, source) -> None:
    if not isinstance(source, dict):
        return

    for key in _SINGULAR_SCOPE_KEYS:
        if target.get(key) in (None, "") and source.get(key) not in (None, ""):
            target[key] = source.get(key)

    for key in _ARRAY_SCOPE_KEYS:
        combined = list(target.get(key) or [])
        combined.extend(list(source.get(key) or []))
        target[key] = _normalise_ids(combined)

    # Group/single push results already contain exact affected IDs. Preserve
    # those instead of reducing a committed group change to one browser row.
    for nested_key in (
        "refresh_scope",
        "push_result",
        "group_propagation",
        "verification_queue",
        "immediate",
        "order_intake",
        "stock_mutation",
        "result",
        "listing_discovery",
    ):
        nested = source.get(nested_key)
        if isinstance(nested, dict):
            _merge_scope(target, nested)


def publish_governed_ui_event(
    *,
    source: str,
    notification_record_id: int | None = None,
    scope: dict | None = None,
) -> int:
    """Publish one committed change and wake sleepers immediately."""
    global _revision

    safe_scope = {}
    _merge_scope(safe_scope, dict(scope or {}))

    # Keep singular identity compatible with existing page selectors while the
    # arrays remain the authoritative complete mutation contract.
    if safe_scope.get("listing_id") in (None, ""):
        ids = safe_scope.get("affected_listing_ids") or []
        if ids:
            safe_scope["listing_id"] = ids[0]
    if safe_scope.get("warehouse_stock_id") in (None, ""):
        ids = safe_scope.get("affected_warehouse_stock_ids") or []
        if ids:
            safe_scope["warehouse_stock_id"] = ids[0]
    if safe_scope.get("group_id") in (None, ""):
        ids = safe_scope.get("affected_group_ids") or []
        if ids:
            safe_scope["group_id"] = ids[0]

    with _condition:
        _revision += 1
        event = {
            "revision": _revision,
            "changed": True,
            "source": str(source or "").strip().lower(),
            "published_at": datetime.utcnow().isoformat() + "Z",
            **safe_scope,
        }
        if notification_record_id is not None:
            event["notification_record_id"] = int(notification_record_id)
        _events.append(event)
        _condition.notify_all()
        return _revision


def publish_webhook_ui_event(
    *,
    platform: str,
    notification_record_id: int,
    scope: dict | None = None,
) -> int:
    """Preserve the existing webhook publisher on the shared UI event path."""
    return publish_governed_ui_event(
        source=f"webhook_{str(platform or '').strip().lower()}",
        notification_record_id=notification_record_id,
        scope={"platform": platform, **dict(scope or {})},
    )


def _events_after(seen_revision: int):
    return [
        dict(event)
        for event in _events
        if int(event.get("revision") or 0) > int(seen_revision)
    ]


def _collapse_events(events: list[dict]) -> dict | None:
    if not events:
        return None

    collapsed = {
        "changed": True,
        "first_revision": events[0]["revision"],
        "revision": events[-1]["revision"],
        "event_count": len(events),
        "affected_listing_ids": [],
        "affected_warehouse_stock_ids": [],
        "affected_group_ids": [],
    }

    for event in events:
        _merge_scope(collapsed, event)
        # latest simple metadata is useful for diagnostics and single-record UI
        for key in (
            "platform",
            "notification_record_id",
            "published_at",
            "event_type",
            "seller_sku",
            "order_id",
            "store_id",
        ):
            if event.get(key) not in (None, ""):
                collapsed[key] = event.get(key)

    if collapsed.get("listing_id") in (None, "") and collapsed["affected_listing_ids"]:
        collapsed["listing_id"] = collapsed["affected_listing_ids"][0]
    if collapsed.get("warehouse_stock_id") in (None, "") and collapsed["affected_warehouse_stock_ids"]:
        collapsed["warehouse_stock_id"] = collapsed["affected_warehouse_stock_ids"][0]
    if collapsed.get("group_id") in (None, "") and collapsed["affected_group_ids"]:
        collapsed["group_id"] = collapsed["affected_group_ids"][0]

    return collapsed


@app.get("/governed/ui/events")
def governed_ui_events():
    """Return immediately for compatibility with already-open browser pages.

    The global bell is now an explicit sales shortcut.  It must not reserve a
    Gunicorn request thread (or cause Flask-Login to open a Neon transaction)
    while a browser waits for an unrelated runtime event.
    """
    with _condition:
        current_revision = _revision

    response = jsonify({
        "ok": True,
        "revision": current_revision,
        # Compatibility callers are deliberately not page-refresh authority.
        "event": None,
    })
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response



@app.get("/governed/ui/events/stream")
def governed_ui_event_stream():
    """Signal-only SSE over the existing governed UI event condition.

    No DB read.
    No marketplace call.
    No polling.
    No second event queue.
    """
    # Flask-Login stores the authenticated user ID in the signed session.
    # Do not resolve current_user here because this connection is long-lived
    # and must never hold a Neon read transaction.
    if not session.get("_user_id"):
        return Response(status=401)

    with _condition:
        initial_revision = int(_revision)

    def _stream():
        seen_revision = initial_revision

        yield "retry: 3000\n\n"

        while True:
            with _condition:
                if int(_revision) == seen_revision:
                    # In-memory sleep only. Zero SQL/API activity.
                    _condition.wait(timeout=25.0)
                current_revision = int(_revision)

            if current_revision != seen_revision:
                seen_revision = current_revision
                yield (
                    "event: marketplace\n"
                    f"data: {seen_revision}\n\n"
                )
            else:
                # Network keepalive only.
                yield ": keepalive\n\n"

    response = Response(
        _stream(),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache, no-transform"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    return response


@event.listens_for(Session, "before_flush")
def _bt38_existing_ui_signal_before_flush(
    session_obj,
    flush_context,
    instances,
):
    """Wake the existing UI signal for canonical marketplace commits.

    Product Linking and webhook routes already publish through this module.
    These model checks cover DB-originating sales/listings and persisted pushes.
    """
    if session_obj.info.get("_bt38_ui_commit_wake"):
        return

    from models import MarketplaceListing, MarketplaceOrder, SyncLog

    for row in session_obj.new:
        if isinstance(row, (MarketplaceListing, MarketplaceOrder)):
            session_obj.info["_bt38_ui_commit_wake"] = True
            return

        if isinstance(row, SyncLog):
            message = str(getattr(row, "message", "") or "").lower()
            if (
                message.startswith("event_type=marketplace_push")
                or message.startswith("event_type=product_linking_")
            ):
                session_obj.info["_bt38_ui_commit_wake"] = True
                return


@event.listens_for(Session, "after_commit")
def _bt38_existing_ui_signal_after_commit(session_obj):
    if session_obj.info.pop("_bt38_ui_commit_wake", False):
        publish_governed_ui_event(
            source="committed_marketplace_state",
            scope={
                "event_type": "committed_marketplace_state",
            },
        )


@event.listens_for(Session, "after_rollback")
def _bt38_existing_ui_signal_after_rollback(session_obj):
    session_obj.info.pop("_bt38_ui_commit_wake", None)

def _result_has_committed_change(value) -> bool:
    if not isinstance(value, dict):
        return False

    for key in (
        "changed",
        "stock_changed",
        "fba_inventory_changed",
        "page_refresh_required",
        "warehouse_refresh_required",
        "created",
        "inserted",
        "imported",
    ):
        if value.get(key) is True:
            return True

    for key in ("rows_updated", "rows_inserted", "created_count", "updated_count"):
        try:
            if int(value.get(key) or 0) > 0:
                return True
        except Exception:
            pass

    if str(value.get("status") or "").strip().lower() in {
        "cancellation_processed",
        "group_processed",
        "warehouse_processed",
        "fba_inventory_updated",
    }:
        return True

    for key in (
        "verification_queue",
        "immediate",
        "order_intake",
        "stock_mutation",
        "push_result",
        "result",
        "listing_discovery",
    ):
        nested = value.get(key)
        if isinstance(nested, dict) and _result_has_committed_change(nested):
            return True

    return False


def _response_has_committed_change(payload) -> bool:
    if not isinstance(payload, dict):
        return False
    return _result_has_committed_change(payload.get("notification_result"))


def _ui_scope_from_response(payload) -> dict:
    if not isinstance(payload, dict):
        return {}
    result = payload.get("notification_result")
    if not isinstance(result, dict):
        return {}
    scope = {}
    _merge_scope(scope, result)
    return scope


@app.after_request
def publish_completed_webhook_and_attach_live_ui(response):
    """Publish changed webhooks and install the shared sleeping browser waiter."""
    path = request.path.rstrip("/") or "/"

    if request.method == "POST" and path in _RELATIONSHIP_EVENT_PATHS:
        payload = response.get_json(silent=True)
        if (
            response.status_code < 400
            and _result_has_committed_change(payload)
        ):
            publish_governed_ui_event(
                source=_RELATIONSHIP_EVENT_PATHS[path],
                scope=payload,
            )
        return response

    if request.method == "POST" and path in _WEBHOOK_PATHS:
        payload = response.get_json(silent=True)
        record_id = getattr(g, "bt38_notification_record_id", None)
        if record_id is None and isinstance(payload, dict):
            record_id = payload.get("notification_record_id")

        failed_after_capture = (
            isinstance(payload, dict)
            and payload.get("status") == "processing_failed"
        )
        committed_change = _response_has_committed_change(payload)

        if (
            record_id is not None
            and response.status_code < 400
            and not failed_after_capture
            and committed_change
        ):
            publish_webhook_ui_event(
                platform=_WEBHOOK_PATHS[path],
                notification_record_id=int(record_id),
                scope=_ui_scope_from_response(payload),
            )
        return response

    if request.method != "GET":
        return response
    if not LIVE_BROWSER_EVENT_WAITER_ENABLED:
        return response
    if "text/html" not in str(response.content_type or "").lower():
        return response

    body = response.get_data(as_text=True)
    if path not in _LIVE_UI_PATHS and 'id="bt38NotificationBell"' not in body:
        return response
    if "bt38WebhookLiveEvents" in body or "</body>" not in body:
        return response

    revision_seed = int(_revision)
    script = r'''
<script id="bt38WebhookLiveEvents">
(function(){
  if (window.bt38WebhookLiveEventsInstalled) return;
  window.bt38WebhookLiveEventsInstalled = true;

  let revision = __BT38_REVISION__;
  let pendingEvent = null;
  let waiting = false;
  let stopped = false;
  let refreshRunning = false;

  function currentPath(){
    return window.location.pathname.replace(/\/$/, "") || "/";
  }

  function ids(values){
    return Array.from(new Set((values || []).filter(function(value){
      return value !== null && value !== undefined && value !== "";
    }).map(String)));
  }

  function exactDetails(contract){
    const rows = [];
    const stockIds = ids(contract?.affected_warehouse_stock_ids);
    const listingIds = ids(contract?.affected_listing_ids);
    const groupIds = ids(contract?.affected_group_ids);
    const max = Math.max(stockIds.length, listingIds.length, groupIds.length, 1);
    for (let i = 0; i < max; i += 1) {
      rows.push({
        ...contract,
        warehouse_stock_id: stockIds[i] || contract?.warehouse_stock_id || null,
        listing_id: listingIds[i] || contract?.listing_id || null,
        group_id: groupIds[i] || contract?.group_id || null
      });
    }
    return rows;
  }

  function escapeSelector(value){
    const text = String(value ?? "");
    if (window.CSS && typeof window.CSS.escape === "function") return window.CSS.escape(text);
    return text.replace(/["\\]/g, "\\$&");
  }

  function selectorFor(detail){
    const path = currentPath();
    if (path === "/warehouse") {
      if (detail?.warehouse_stock_id != null) return `tr[data-stock-id="${escapeSelector(detail.warehouse_stock_id)}"]`;
      if (detail?.listing_id != null) return `tr[data-listing-id="${escapeSelector(detail.listing_id)}"]`;
      if (detail?.seller_sku) return `tr[data-sku="${escapeSelector(detail.seller_sku)}"]`;
      if (detail?.group_id != null) return `tr[data-group-id="${escapeSelector(detail.group_id)}"]`;
    }
    if (path === "/amazon-fba-stock" && detail?.seller_sku) return `tr[data-bt38-seller-sku="${escapeSelector(detail.seller_sku)}"]`;
    if (path === "/orders-mcf" && detail?.order_id) return `tr[data-bt38-order-id="${escapeSelector(detail.order_id)}"]`;
    if (path === "/listings") {
      if (detail?.listing_id != null) return `tr[data-listing-id="${escapeSelector(detail.listing_id)}"]`;
      if (detail?.seller_sku) return `tr[data-sku="${escapeSelector(detail.seller_sku)}"]`;
    }
    return null;
  }

  function markRows(root){
    const path = currentPath();
    if (path === "/amazon-fba-stock") {
      root.querySelectorAll("table tbody tr").forEach(function(row){
        const sku = row.querySelector("td code")?.textContent?.trim();
        if (sku) row.dataset.bt38SellerSku = sku;
      });
    }
    if (path === "/orders-mcf") {
      root.querySelectorAll(".mcf-order-row").forEach(function(row){
        const orderId = row.querySelector("td:nth-child(2) strong")?.textContent?.trim();
        if (orderId) row.dataset.bt38OrderId = orderId;
      });
    }
  }

  function targetedUrl(detail){
    const url = new URL(window.location.href);
    const path = currentPath();
    if (path === "/warehouse" && detail?.seller_sku) {
      url.searchParams.set("q", String(detail.seller_sku));
      url.searchParams.set("page", "1");
      url.searchParams.set("per_page", "15");
    }
    if (path === "/amazon-fba-stock" && detail?.seller_sku) {
      url.searchParams.set("search", String(detail.seller_sku));
      url.searchParams.set("status", "all");
      url.searchParams.set("page", "1");
      url.searchParams.set("per_page", "15");
    }
    return url.toString();
  }

  async function refreshHtmlRow(detail){
    const path = currentPath();
    if (path === "/product-linking" || path === "/orders-mcf") return;
    markRows(document);
    const selector = selectorFor(detail);
    if (!selector) return;
    const currentRow = document.querySelector(selector);
    if (!currentRow) return;

    const controller = new AbortController();
    const timeout = window.setTimeout(function(){ controller.abort(); }, 1500);
    try {
      const response = await fetch(targetedUrl(detail), {
        credentials: "same-origin",
        cache: "no-store",
        headers: {"X-BT38-UI-Refresh": "targeted"},
        signal: controller.signal
      });
      if (!response.ok) return;
      const html = await response.text();
      const parsed = new DOMParser().parseFromString(html, "text/html");
      markRows(parsed);
      const freshRow = parsed.querySelector(selector);
      if (freshRow) currentRow.replaceWith(document.importNode(freshRow, true));
    } catch (error) {
      if (error?.name !== "AbortError") console.warn("[BT38 UI] targeted refresh failed", error);
    } finally {
      window.clearTimeout(timeout);
    }
  }

  async function handleEvent(contract){
    if (!contract || refreshRunning) return;
    refreshRunning = true;
    try {
      window.dispatchEvent(new CustomEvent("bt38-marketplace-event", {detail: contract}));
      if (currentPath() === "/product-linking" && typeof window.bt38ApplyProductLinkingMutation === "function") {
        const identity = {
          warehouseId: contract.warehouse_stock_id,
          groupId: contract.group_id,
          listingId: contract.listing_id,
          listingSku: contract.seller_sku,
          warehouseSku: contract.seller_sku
        };
        await window.bt38ApplyProductLinkingMutation(contract, identity);
        return;
      }

      for (const detail of exactDetails(contract)) await refreshHtmlRow(detail);
    } finally {
      refreshRunning = false;
    }
  }

  async function waitForNextEvent(){
    if (stopped || waiting) return;
    waiting = true;
    try {
      const response = await fetch(
        "/governed/ui/events?after=" + encodeURIComponent(revision),
        {credentials: "same-origin", cache: "no-store"}
      );
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      revision = Math.max(revision, Number(payload?.revision || 0));
      const detail = payload?.event || null;
      if (detail) {
        if (document.hidden) pendingEvent = detail;
        else await handleEvent(detail);
      }
    } catch (error) {
      if (!stopped) console.warn("[BT38 UI] event wait unavailable", error);
    } finally {
      waiting = false;
      if (!stopped) window.setTimeout(waitForNextEvent, 50);
    }
  }

  document.addEventListener("visibilitychange", function(){
    if (!document.hidden && pendingEvent) {
      const detail = pendingEvent;
      pendingEvent = null;
      void handleEvent(detail);
    }
  });

  window.addEventListener("beforeunload", function(){ stopped = true; }, {once: true});

  // Page sessions remain unchanged. Runtime events no longer create a
  // permanent browser request; the notification bell pulls sales on demand.
  function start(){ markRows(document); }
  if (document.readyState === "complete") window.setTimeout(start, 0);
  else window.addEventListener("load", function(){ window.setTimeout(start, 0); }, {once: true});
})();
</script>
'''.replace("__BT38_REVISION__", str(revision_seed))

    response.set_data(body.replace("</body>", script + "\n</body>", 1))
    return response