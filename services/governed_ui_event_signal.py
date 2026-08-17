"""Event-driven DB -> UI handoff for governed BT38 pages.

Contract:
- publish only after a committed governed change
- preserve every unseen committed revision in a bounded in-memory queue
- carry exact affected listing / Warehouse / group identities
- no browser long-poll, Neon polling, broad scans, or full-page reloads
- the global bell pulls sales only when explicitly opened
"""
from __future__ import annotations

import json
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
        ui_event = {
            "revision": _revision,
            "changed": True,
            "source": str(source or "").strip().lower(),
            "published_at": datetime.utcnow().isoformat() + "Z",
            **safe_scope,
        }
        if notification_record_id is not None:
            ui_event["notification_record_id"] = int(notification_record_id)
        _events.append(ui_event)
        _condition.notify_all()
        return _revision


def publish_webhook_ui_event(
    *,
    platform: str,
    notification_record_id: int,
    scope: dict | None = None,
) -> int:
    return publish_governed_ui_event(
        source=f"webhook_{str(platform or '').strip().lower()}",
        notification_record_id=notification_record_id,
        scope={"platform": platform, **dict(scope or {})},
    )


def _events_after(seen_revision: int):
    return [
        dict(ui_event)
        for ui_event in _events
        if int(ui_event.get("revision") or 0) > int(seen_revision)
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

    for ui_event in events:
        _merge_scope(collapsed, ui_event)
        for key in (
            "platform",
            "notification_record_id",
            "published_at",
            "event_type",
            "seller_sku",
            "order_id",
            "store_id",
        ):
            if ui_event.get(key) not in (None, ""):
                collapsed[key] = ui_event.get(key)

    if collapsed.get("listing_id") in (None, "") and collapsed["affected_listing_ids"]:
        collapsed["listing_id"] = collapsed["affected_listing_ids"][0]
    if collapsed.get("warehouse_stock_id") in (None, "") and collapsed["affected_warehouse_stock_ids"]:
        collapsed["warehouse_stock_id"] = collapsed["affected_warehouse_stock_ids"][0]
    if collapsed.get("group_id") in (None, "") and collapsed["affected_group_ids"]:
        collapsed["group_id"] = collapsed["affected_group_ids"][0]

    return collapsed


@app.get("/governed/ui/events")
def governed_ui_events():
    with _condition:
        current_revision = _revision

    response = jsonify({
        "ok": True,
        "revision": current_revision,
        "event": None,
    })
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.get("/governed/ui/events/stream")
def governed_ui_event_stream():
    """Signal-only SSE over the existing governed UI event condition.

    No DB read. No marketplace call. No polling. No second event queue.
    The payload is the already-committed in-memory scope for the changed rows.
    """
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
                    _condition.wait(timeout=25.0)
                current_revision = int(_revision)
                pending_events = (
                    _events_after(seen_revision)
                    if current_revision != seen_revision
                    else []
                )

            if current_revision != seen_revision:
                contract = _collapse_events(pending_events) or {
                    "changed": True,
                    "revision": current_revision,
                }
                seen_revision = current_revision
                yield (
                    "event: marketplace\n"
                    f"data: {json.dumps(contract, separators=(',', ':'))}\n\n"
                )
            else:
                yield ": keepalive\n\n"

    response = Response(_stream(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache, no-transform"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    return response


def _sync_log_push_scope(row) -> dict:
    """Recover exact push identity from the persisted governed SyncLog line."""
    message = str(getattr(row, "message", "") or "").strip()
    values = {}
    for token in message.split():
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        values[key.strip().lower()] = value.strip()

    scope = {
        "event_type": values.get("event_type") or "marketplace_push",
        "seller_sku": values.get("sku"),
        "store_id": values.get("store_id"),
        "listing_id": values.get("listing_id"),
        "warehouse_stock_id": values.get("warehouse_stock_id"),
        "group_id": values.get("group_id"),
    }
    for singular, plural in (
        ("listing_id", "affected_listing_ids"),
        ("warehouse_stock_id", "affected_warehouse_stock_ids"),
        ("group_id", "affected_group_ids"),
    ):
        value = scope.get(singular)
        if value not in (None, "", "None"):
            scope[plural] = [value]
        elif value == "None":
            scope[singular] = None
    return scope


@event.listens_for(Session, "before_flush")
def _bt38_existing_ui_signal_before_flush(session_obj, flush_context, instances):
    if session_obj.info.get("_bt38_ui_commit_wake"):
        return

    from models import MarketplaceListing, MarketplaceOrder, SyncLog

    for row in session_obj.new:
        if isinstance(row, MarketplaceListing):
            session_obj.info["_bt38_ui_commit_wake"] = True
            session_obj.info["_bt38_ui_commit_scope"] = {
                "event_type": "marketplace_listing",
                "seller_sku": getattr(row, "external_sku", None),
                "listing_id": getattr(row, "id", None),
                "warehouse_stock_id": getattr(row, "warehouse_stock_id", None),
                "group_id": getattr(row, "master_product_group_id", None),
            }
            return

        if isinstance(row, MarketplaceOrder):
            session_obj.info["_bt38_ui_commit_wake"] = True
            session_obj.info["_bt38_ui_commit_scope"] = {
                "event_type": "marketplace_order",
                "seller_sku": getattr(row, "sku", None),
                "order_id": getattr(row, "marketplace_order_id", None),
                "warehouse_stock_id": getattr(row, "warehouse_stock_id", None),
                "store_id": getattr(row, "store_id", None),
            }
            return

        if isinstance(row, SyncLog):
            message = str(getattr(row, "message", "") or "").lower()
            if message.startswith("event_type=marketplace_push"):
                session_obj.info["_bt38_ui_commit_wake"] = True
                session_obj.info["_bt38_ui_commit_scope"] = _sync_log_push_scope(row)
                return
            if message.startswith("event_type=product_linking_"):
                session_obj.info["_bt38_ui_commit_wake"] = True
                session_obj.info["_bt38_ui_commit_scope"] = {
                    "event_type": "product_linking_change",
                }
                return


@event.listens_for(Session, "after_commit")
def _bt38_existing_ui_signal_after_commit(session_obj):
    should_wake = session_obj.info.pop("_bt38_ui_commit_wake", False)
    scope = session_obj.info.pop("_bt38_ui_commit_scope", None) or {
        "event_type": "committed_marketplace_state",
    }
    if should_wake:
        publish_governed_ui_event(
            source="committed_marketplace_state",
            scope=scope,
        )


@event.listens_for(Session, "after_rollback")
def _bt38_existing_ui_signal_after_rollback(session_obj):
    session_obj.info.pop("_bt38_ui_commit_wake", None)
    session_obj.info.pop("_bt38_ui_commit_scope", None)


def _result_has_committed_change(value) -> bool:
    if not isinstance(value, dict):
        return False
    for key in (
        "changed", "stock_changed", "fba_inventory_changed",
        "page_refresh_required", "warehouse_refresh_required",
        "created", "inserted", "imported",
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
        "cancellation_processed", "group_processed", "warehouse_processed",
        "fba_inventory_updated",
    }:
        return True
    for key in (
        "verification_queue", "immediate", "order_intake", "stock_mutation",
        "push_result", "result", "listing_discovery",
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
    """Publish changed webhooks; the old browser waiter remains retired."""
    path = request.path.rstrip("/") or "/"

    if request.method == "POST" and path in _RELATIONSHIP_EVENT_PATHS:
        payload = response.get_json(silent=True)
        if response.status_code < 400 and _result_has_committed_change(payload):
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

    # The legacy injected browser waiter is deliberately retired. The shared
    # base.html EventSource consumes /governed/ui/events/stream instead.
    return response
