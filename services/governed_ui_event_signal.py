"""Shared event-driven UI freshness for governed BT38 pages.

Contract:
- No marketplace event means no Neon polling and no marketplace polling.
- Governed pages finish their normal navigation before event waiting starts.
- One ordinary sleeping request waits for the next in-process event revision.
- The wait response closes on event or timeout, so no permanent HTTP stream is
  attached to the page lifecycle.
- Marketplace events carry exact affected identities; open pages refresh only
  that record/group from BT38 DB truth.
- Governed data pages use 15/25/50/100 paging.
"""
from __future__ import annotations

import threading
from datetime import datetime

from flask import g, has_request_context, jsonify, request
from flask_login import login_required

from app import app


_condition = threading.Condition()
_revision = 0
_latest_event: dict | None = None

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

_UI_SCOPE_KEYS = (
    "event_type",
    "seller_sku",
    "listing_id",
    "order_id",
    "warehouse_stock_id",
    "group_id",
    "store_id",
)

_PAGE_SIZES = {15, 25, 50, 100}


def _requested_page_size(default: int = 15) -> int:
    try:
        value = int(request.args.get("per_page") or default)
    except Exception:
        value = default
    return value if value in _PAGE_SIZES else default


def _install_fba_paging_alignment() -> None:
    """Keep the existing FBA query path but align its page-size contract."""
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


def publish_webhook_ui_event(
    *,
    platform: str,
    notification_record_id: int,
    scope: dict | None = None,
) -> int:
    """Wake sleeping UI waits after governed webhook processing completes."""
    global _revision, _latest_event

    safe_scope = {
        key: value
        for key, value in dict(scope or {}).items()
        if key in _UI_SCOPE_KEYS and value not in (None, "")
    }

    with _condition:
        _revision += 1
        _latest_event = {
            "revision": _revision,
            "platform": str(platform or "").strip().lower(),
            "notification_record_id": int(notification_record_id),
            "published_at": datetime.utcnow().isoformat() + "Z",
            **safe_scope,
        }
        _condition.notify_all()
        return _revision


@app.get("/governed/ui/events")
@login_required
def governed_ui_events():
    """Wait for one later event revision without touching Neon."""
    try:
        seen_revision = max(0, int(request.args.get("after") or 0))
    except Exception:
        seen_revision = 0

    with _condition:
        if _revision <= seen_revision:
            _condition.wait(timeout=25.0)
        current_revision = _revision
        event = dict(_latest_event or {})

    response = jsonify({
        "ok": True,
        "revision": current_revision,
        "event": event if current_revision > seen_revision and event else None,
    })
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


def _ui_scope_from_response(payload) -> dict:
    if not isinstance(payload, dict):
        return {}

    result = payload.get("notification_result")
    if not isinstance(result, dict):
        return {}

    scope = {
        key: result.get(key)
        for key in _UI_SCOPE_KEYS
        if result.get(key) not in (None, "")
    }

    refresh_scope = result.get("refresh_scope")
    if isinstance(refresh_scope, dict):
        for key in _UI_SCOPE_KEYS:
            if (
                scope.get(key) in (None, "")
                and refresh_scope.get(key) not in (None, "")
            ):
                scope[key] = refresh_scope.get(key)

    return scope


@app.after_request
def publish_completed_webhook_and_attach_live_ui(response):
    """Publish completed webhook events and attach the shared sleeping waiter."""
    path = request.path.rstrip("/") or "/"

    if request.method == "POST" and path in _WEBHOOK_PATHS:
        payload = response.get_json(silent=True)
        record_id = getattr(g, "bt38_notification_record_id", None)
        if record_id is None and isinstance(payload, dict):
            record_id = payload.get("notification_record_id")

        failed_after_capture = (
            isinstance(payload, dict)
            and payload.get("status") == "processing_failed"
        )

        if (
            record_id is not None
            and response.status_code < 400
            and not failed_after_capture
        ):
            publish_webhook_ui_event(
                platform=_WEBHOOK_PATHS[path],
                notification_record_id=int(record_id),
                scope=_ui_scope_from_response(payload),
            )

        return response

    if request.method != "GET" or path not in _LIVE_UI_PATHS:
        return response

    content_type = str(response.content_type or "").lower()
    if "text/html" not in content_type:
        return response

    body = response.get_data(as_text=True)
    if "bt38WebhookLiveEvents" in body or "</body>" not in body:
        return response

    revision_seed = int(_revision)
    script = r'''
<script id="bt38WebhookLiveEvents">
(function(){
  if (window.bt38WebhookLiveEventsInstalled) return;
  window.bt38WebhookLiveEventsInstalled = true;

  const PAGE_SIZES = [15, 25, 50, 100];
  let revision = __BT38_REVISION__;
  let pendingEvent = null;
  let waiting = false;
  let stopped = false;
  let targetedRefreshRunning = false;

  function escapeSelector(value){
    const text = String(value ?? "");
    if (window.CSS && typeof window.CSS.escape === "function") {
      return window.CSS.escape(text);
    }
    return text.replace(/["\\]/g, "\\$&");
  }

  function currentPath(){
    return window.location.pathname.replace(/\/$/, "") || "/";
  }

  function normalisePageSize(value){
    const parsed = Number.parseInt(value, 10);
    return PAGE_SIZES.includes(parsed) ? parsed : 15;
  }

  function setupFbaPaging(){
    if (currentPath() !== "/amazon-fba-stock") return;

    const currentUrl = new URL(window.location.href);
    const pageSize = normalisePageSize(
      currentUrl.searchParams.get("per_page") || 15
    );

    document.querySelectorAll('form[action*="amazon-fba-stock"]').forEach(function(form){
      let hidden = form.querySelector('input[name="per_page"]');
      if (!hidden) {
        hidden = document.createElement("input");
        hidden.type = "hidden";
        hidden.name = "per_page";
        form.appendChild(hidden);
      }
      hidden.value = String(pageSize);
    });

    document.querySelectorAll(
      '.nav-pills a[href*="amazon-fba-stock"], .pagination a.page-link'
    ).forEach(function(link){
      try {
        const url = new URL(link.href, window.location.origin);
        url.searchParams.set("per_page", String(pageSize));
        link.href = url.toString();
      } catch (_) {}
    });

    const inventoryHeader = Array.from(
      document.querySelectorAll(".card-header")
    ).find(function(node){
      return String(node.textContent || "").includes("FBA Inventory");
    });

    if (!inventoryHeader || document.getElementById("bt38FbaPageSize")) return;

    const wrapper = document.createElement("div");
    wrapper.className = "d-flex align-items-center gap-2 ms-2";
    wrapper.innerHTML =
      '<label class="small text-muted mb-0" for="bt38FbaPageSize">Rows</label>' +
      '<select id="bt38FbaPageSize" class="form-select form-select-sm" style="width:auto">' +
      PAGE_SIZES.map(function(size){
        return '<option value="' + size + '"' +
          (size === pageSize ? ' selected' : '') + '>' + size + '</option>';
      }).join("") + '</select>';
    inventoryHeader.appendChild(wrapper);

    wrapper.querySelector("select").addEventListener("change", function(event){
      const size = normalisePageSize(event.target.value);
      const url = new URL(window.location.href);
      url.searchParams.set("per_page", String(size));
      url.searchParams.set("page", "1");
      window.location.assign(url.toString());
    });
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

  function selectorFor(detail){
    const path = currentPath();
    const stockId = detail?.warehouse_stock_id;
    const listingId = detail?.listing_id;
    const groupId = detail?.group_id;
    const sellerSku = detail?.seller_sku;
    const orderId = detail?.order_id;

    if (path === "/warehouse") {
      if (stockId != null) return `tr[data-stock-id="${escapeSelector(stockId)}"]`;
      if (listingId != null) return `tr[data-listing-id="${escapeSelector(listingId)}"]`;
      if (sellerSku) return `tr[data-sku="${escapeSelector(sellerSku)}"]`;
      if (groupId != null) return `tr[data-group-id="${escapeSelector(groupId)}"]`;
    }

    if (path === "/amazon-fba-stock" && sellerSku) {
      return `tr[data-bt38-seller-sku="${escapeSelector(sellerSku)}"]`;
    }

    if (path === "/orders-mcf" && orderId) {
      return `tr[data-bt38-order-id="${escapeSelector(orderId)}"]`;
    }

    if (path === "/listings") {
      if (listingId != null) return `tr[data-listing-id="${escapeSelector(listingId)}"]`;
      if (sellerSku) return `tr[data-sku="${escapeSelector(sellerSku)}"]`;
    }

    return null;
  }

  function targetedUrl(detail){
    const path = currentPath();
    const url = new URL(window.location.href);

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

  async function refreshAffectedHtmlRecord(detail){
    const path = currentPath();

    if (path === "/product-linking" || path === "/orders-mcf") return;

    markRows(document);
    const selector = selectorFor(detail);
    if (!selector) return;

    const currentRow = document.querySelector(selector);
    if (!currentRow) return;

    const controller = new AbortController();
    const timeout = window.setTimeout(function(){ controller.abort(); }, 5000);

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
      if (!freshRow) return;

      currentRow.replaceWith(document.importNode(freshRow, true));
      if (window.feather && typeof window.feather.replace === "function") {
        window.feather.replace();
      }
    } catch (error) {
      if (error?.name !== "AbortError") {
        console.warn("[BT38 UI] targeted event refresh failed", error);
      }
    } finally {
      window.clearTimeout(timeout);
    }
  }

  async function handleEvent(detail){
    if (!detail || targetedRefreshRunning) return;
    targetedRefreshRunning = true;
    try {
      window.dispatchEvent(
        new CustomEvent("bt38-marketplace-event", {detail: detail})
      );
      await refreshAffectedHtmlRecord(detail);
    } finally {
      targetedRefreshRunning = false;
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
      if (!stopped) {
        console.warn("[BT38 UI] event wait unavailable", error);
      }
    } finally {
      waiting = false;
      if (!stopped) window.setTimeout(waitForNextEvent, 100);
    }
  }

  document.addEventListener("visibilitychange", function(){
    if (!document.hidden && pendingEvent) {
      const detail = pendingEvent;
      pendingEvent = null;
      void handleEvent(detail);
    }
  });

  window.addEventListener("beforeunload", function(){
    stopped = true;
  }, {once: true});

  function start(){
    setupFbaPaging();
    markRows(document);
    void waitForNextEvent();
  }

  if (document.readyState === "complete") {
    window.setTimeout(start, 0);
  } else {
    window.addEventListener("load", function(){
      window.setTimeout(start, 0);
    }, {once: true});
  }
})();
</script>
'''.replace("__BT38_REVISION__", str(revision_seed))

    response.set_data(body.replace("</body>", script + "\n</body>", 1))
    return response
