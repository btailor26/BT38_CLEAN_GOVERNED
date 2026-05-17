#!/usr/bin/env python3
"""BT38 full governed worker rewrite audit.

Static/in-memory proof only. This script does not instantiate marketplace API
clients and does not call Amazon/eBay network methods.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import os
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
RETIRED_ERROR = "Legacy direct marketplace execution route is retired. Use governed dispatcher execution."


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"PASS: {message}")


def route_function_body(route_text: str, function_name: str) -> str:
    marker = f"def {function_name}"
    start = route_text.find(marker)
    require(start >= 0, f"routes.py contains {function_name}")
    next_func = route_text.find("\ndef ", start + 1)
    next_route = route_text.find("\n@bp.", start + 1)
    candidates = [idx for idx in (next_func, next_route) if idx > start]
    end = min(candidates) if candidates else len(route_text)
    return route_text[start:end]



def route_block_by_rule(route_text: str, route_rule: str) -> str:
    marker = f"@bp.route('{route_rule}'"
    start = route_text.find(marker)
    require(start >= 0, f"routes.py contains retired route {route_rule}")
    next_route = route_text.find("\n@bp.route", start + 1)
    end = next_route if next_route > start else len(route_text)
    return route_text[start:end]

def assert_static_rewrite() -> None:
    routes = read("routes.py")
    dispatcher = read("sync_dispatcher.py")
    queue = read("queue_manager.py")
    smart = read("smart_push_service.py")
    sync_service = read("sync_service.py")
    app = read("app.py")
    main = read("main.py")

    require("sync_item_to_store(" not in routes, "no direct route calls sync_item_to_store")
    require("immediate_sync_store(" not in routes, "no direct route calls immediate_sync_store")
    require("push_quantity_only(" not in routes, "no route calls eBayAPIService.push_quantity_only directly")
    require("sync_item_to_store" not in dispatcher, "sync_dispatcher no longer references sync_item_to_store")
    require("execute_governed_push" in dispatcher and "smart_push_service" in dispatcher, "sync_dispatcher calls smart_push_service governed execution")
    require("from sync_service import sync_warehouse_stock_to_store" not in dispatcher, "sync_dispatcher no longer imports sync_warehouse_stock_to_store for push execution")

    push_guard_pos = queue.find("if job_type == JOB_PUSH_ITEM")
    syncjob_pos = queue.find("job = SyncJob(")
    runtime_pos = queue.find("is_runtime_action_allowed", push_guard_pos)
    market_pos = queue.find("assert_marketplace_push_allowed", push_guard_pos)
    require(push_guard_pos >= 0 and syncjob_pos > push_guard_pos, "enqueue_sync_job has a JOB_PUSH_ITEM pre-create guard block")
    require(push_guard_pos < runtime_pos < syncjob_pos, "queue_manager enforces runtime gate before SyncJob creation")
    require(push_guard_pos < market_pos < syncjob_pos, "queue_manager enforces marketplace eligibility before SyncJob creation")

    individual = route_function_body(routes, "push_stock_individual")
    bulk = route_function_body(routes, "push_stock_bulk")
    require("enqueue_sync_job" in individual and "JOB_PUSH_ITEM" in individual, "/push_stock/<id> queues through enqueue_sync_job")
    require("sync_item_to_store" not in individual and "AmazonAPIService" not in individual and "eBayAPIService" not in individual, "/push_stock/<id> does not call direct push services")
    require("enqueue_sync_job" in bulk and "JOB_PUSH_ITEM" in bulk, "/push_stock_bulk queues through enqueue_sync_job")
    require("sync_item_to_store" not in bulk and "AmazonAPIService" not in bulk and "eBayAPIService" not in bulk, "/push_stock_bulk does not call direct push services")

    retired_routes = [
        "/api/test/ebay-push/<int:item_id>",
        "/test/ebay-push",
        "/api/push-sku",
        "/api/sync/amazon/sku/<sku>",
        "/api/sync/ebay/sku/<sku>",
        "/groups/<int:group_id>/push",
        "/stores/sync/<int:store_id>",
        "/push_stock_all",
        "/api/listings/<int:listing_id>/push",
        "/sync/run/<int:store_id>",
        "/debug/fba-local",
        "/debug/fba-local-direct",
        "/debug/fba-open",
    ]
    for route_rule in retired_routes:
        block = route_block_by_rule(routes, route_rule)
        direct_retired = RETIRED_ERROR in block and "route_retired" in block and "410" in block
        helper_retired = "_retired_marketplace_execution_response" in block and "def _retired_marketplace_execution_response" in routes and RETIRED_ERROR in routes and "route_retired" in routes and "410" in routes
        require(direct_retired or helper_retired, f"{route_rule} returns controlled 410 retirement response")

    require("start_dispatcher()" not in app, "no dispatcher worker starts on app import")
    require("start_order_import_scheduler()" not in app, "no order import scheduler starts on app import")
    require("threading.Thread(target=start_sync_service" not in main, "no legacy sync service thread starts from main.py")

    require("def sync_item_to_store" in sync_service and "[GOVERNED_PATH_ONLY]" in sync_service, "sync_service direct push helpers are fail-closed")
    require("def execute_governed_push" in smart, "smart_push_service exposes governed push execution entrypoint")
    require("marketplace_push_eligibility" in smart, "smart_push_service re-checks marketplace/FBA guard before marketplace service")
    require("is_runtime_action_allowed" in smart, "smart_push_service re-checks runtime gate before marketplace service")

    sync_route_mutation = re.search(r"def manual_sync_store[\s\S]*?(?=\n@bp\.|\Z)", routes)
    require(sync_route_mutation is not None and "available_quantity =" not in sync_route_mutation.group(0), "sync route does not mutate warehouse quantity")


def assert_fba_fbm_guard() -> None:
    from fba_fbm_helpers import marketplace_push_eligibility

    amazon = SimpleNamespace(
        id=1,
        name="Amazon FBM",
        platform="Amazon",
        fulfillment_type="FBM",
        fbm_sync_enabled=True,
        fba_import_enabled=False,
    )
    ebay = SimpleNamespace(id=2, name="eBay", platform="eBay")

    def listing(channel):
        return SimpleNamespace(amazon_fulfillment_channel=channel)

    def stock(sku="SKU-1", location="A1"):
        return SimpleNamespace(sku=sku, location=location)

    allowed, reason = marketplace_push_eligibility(amazon, sku="FBA-KA-OL-100-ML", listing=listing("MFN"), query_database=False)
    require(not allowed and "FBA-" in reason, "FBA-KA-OL-100-ML blocked")

    allowed, reason = marketplace_push_eligibility(amazon, sku="SKU-UNKNOWN", listing=listing(None), query_database=False)
    require(not allowed and "unknown/blank" in reason, "Amazon unknown fulfilment blocked")

    allowed, reason = marketplace_push_eligibility(amazon, sku="SKU-DEFAULT", listing=listing("DEFAULT"), query_database=False)
    require(not allowed and "FBA/read-only" in reason, "Amazon DEFAULT fulfilment blocked")

    allowed, reason = marketplace_push_eligibility(amazon, sku="SKU-MFN", warehouse_stock=stock(), listing=listing("MFN"), query_database=False)
    require(allowed, "explicit Amazon MFN allowed")

    allowed, reason = marketplace_push_eligibility(ebay, sku="SKU-EBAY", query_database=False)
    require(allowed, "eBay allowed by marketplace guard")


def assert_app_routes() -> None:
    routes = read("routes.py")
    require("@bp.route('/push_stock/<int:item_id>'" in routes, "frontend-called /push_stock/<int:item_id> route exists")
    require("@bp.route('/push_stock_bulk'" in routes, "frontend-called /push_stock_bulk route exists")
    require("@bp.route('/push_stock_all'" in routes, "/push_stock_all retired route exists")

    push_all = route_function_body(routes, "push_stock_all")
    require('"execution_blocked": True' in push_all, "/push_stock_all sets execution_blocked true")
    require('"route_retired": True' in push_all, "/push_stock_all sets route_retired true")
    require(RETIRED_ERROR in push_all and '410' in push_all, "/push_stock_all returns controlled HTTP 410 JSON statically")


def main() -> int:
    print("BT38 Full Governed Worker Rewrite Audit")
    print("Mode: local static/in-memory checks only; no marketplace API calls")
    assert_static_rewrite()
    assert_fba_fbm_guard()
    assert_app_routes()
    print("PASS: audit script made no marketplace API calls")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"FAIL: {exc}")
        sys.exit(1)
