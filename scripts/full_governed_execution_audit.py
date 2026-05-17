#!/usr/bin/env python3
"""BT38 controlled-collapse proof for governed marketplace execution.

This is a static/in-memory audit. It does not import Flask, instantiate
marketplace API clients, create feeds, enqueue jobs, or call live APIs.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RETIRED_ERROR = "Legacy direct marketplace execution route is retired. Use governed dispatcher execution."

EXECUTION_MAP = [
    ("/push_stock/<int:item_id>", "push_stock_individual", "queue_manager.enqueue_sync_job", "SyncJob only", "GOVERNED"),
    ("/push_stock_bulk", "push_stock_bulk", "queue_manager.enqueue_sync_job", "SyncJob only", "GOVERNED"),
    ("/api/admin/amazon/set-price-and-push/<sku>", "admin_set_price_and_push_amazon", "queue_manager.enqueue_sync_job", "SyncJob only", "GOVERNED"),
    ("/push_stock_all", "push_stock_all", "retired 410", "none", "LEGACY_REMOVE_CANDIDATE"),
    ("/groups/<int:group_id>/push", "push_group_stock", "retired 410", "none", "LEGACY_REMOVE_CANDIDATE"),
    ("/api/sync/amazon/sku/<sku>", "api_sync_amazon_sku", "retired 410", "none", "LEGACY_REMOVE_CANDIDATE"),
    ("/api/sync/ebay/sku/<sku>", "api_sync_ebay_sku", "retired 410", "none", "LEGACY_REMOVE_CANDIDATE"),
    ("/sync/run/<int:store_id>", "sync_run_store_retired", "retired 410", "none", "LEGACY_REMOVE_CANDIDATE"),
    ("/api/test/ebay-push/<int:item_id>", "api_test_ebay_push_retired", "retired 410", "none", "DEBUG_ONLY"),
    ("/test/ebay-push", "test_ebay_push_retired", "retired 410", "none", "DEBUG_ONLY"),
    ("/debug/fba-local", "debug_fba_local_retired", "retired 410", "none", "DEBUG_ONLY"),
    ("/debug/fba-local-direct", "debug_fba_local_direct_retired", "retired 410", "none", "DEBUG_ONLY"),
    ("/debug/fba-open", "debug_fba_open_retired", "retired 410", "none", "DEBUG_ONLY"),
    ("sync_dispatcher JOB_PUSH_ITEM", "_execute_push_item/_execute_push_warehouse_item", "smart_push_service.execute_governed_push", "marketplace client after guards", "GOVERNED"),
    ("sync_service.sync_item_to_store", "sync_item_to_store", "fail-closed", "none", "TRANSITIONAL"),
]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"PASS: {message}")


def route_block(route_text: str, route_rule: str) -> str:
    marker = f"@bp.route('{route_rule}'"
    start = route_text.find(marker)
    require(start >= 0, f"route exists: {route_rule}")
    next_route = route_text.find("\n@bp.route", start + 1)
    end = next_route if next_route > start else len(route_text)
    return route_text[start:end]


def function_block(text: str, function_name: str) -> str:
    marker = f"def {function_name}"
    start = text.find(marker)
    require(start >= 0, f"function exists: {function_name}")
    next_func = text.find("\ndef ", start + 1)
    next_method = text.find("\n    def ", start + 1)
    candidates = [idx for idx in (next_func, next_method) if idx > start]
    end = min(candidates) if candidates else len(text)
    return text[start:end]


def print_execution_map() -> None:
    print("Execution map:")
    for route, function, service, marketplace_call, classification in EXECUTION_MAP:
        print(f"- {classification}: {route} -> {function} -> {service} -> {marketplace_call}")


def assert_routes(routes: str) -> None:
    for route_rule in [
        "/push_stock_all",
        "/groups/<int:group_id>/push",
        "/api/sync/amazon/sku/<sku>",
        "/api/sync/ebay/sku/<sku>",
        "/sync/run/<int:store_id>",
        "/api/test/ebay-push/<int:item_id>",
        "/test/ebay-push",
        "/debug/fba-local",
        "/debug/fba-local-direct",
        "/debug/fba-open",
        "/api/listings/<int:listing_id>/push",
    ]:
        block = route_block(routes, route_rule)
        direct = RETIRED_ERROR in block and "route_retired" in block and "410" in block
        helper = "_retired_marketplace_execution_response" in block and RETIRED_ERROR in routes and "route_retired" in routes and "410" in routes
        require(direct or helper, f"unsafe route retired with governed 410: {route_rule}")

    for function_name in ["push_stock_individual", "push_stock_bulk", "admin_set_price_and_push_amazon"]:
        block = function_block(routes, function_name)
        require("enqueue_sync_job" in block and "JOB_PUSH_ITEM" in block, f"{function_name} queues through queue_manager")
        require("sync_item_to_store" not in block, f"{function_name} does not call sync_item_to_store")
        require("push_quantity_only" not in block and "create_feed" not in block, f"{function_name} does not call marketplace APIs directly")

    bulk_action = function_block(routes, "bulk_listing_action")
    require("last_push_status = 'pending'" not in bulk_action, "bulk listing push action does not write fake pending push status")
    require("_retired_marketplace_execution_response" in bulk_action, "bulk listing push action is retired/fail-closed")

    route_push_blocks = "\n".join(function_block(routes, name) for name in ["push_stock_individual", "push_stock_bulk", "push_stock_all"])
    require("available_quantity =" not in route_push_blocks and ".quantity =" not in route_push_blocks, "routes do not mutate warehouse quantity during push")
    require("push_quantity_only(" not in routes, "no route directly calls eBayAPIService.push_quantity_only")
    require("create_feed" not in routes and "POST_INVENTORY_AVAILABILITY_DATA" not in routes, "no route directly calls Amazon feed creation")


def assert_queue(queue: str) -> None:
    push_guard = queue.find("if job_type == JOB_PUSH_ITEM")
    syncjob = queue.find("job = SyncJob(")
    require(push_guard >= 0 and syncjob > push_guard, "queue_manager has JOB_PUSH_ITEM pre-create block")
    require(push_guard < queue.find("is_runtime_action_allowed", push_guard) < syncjob, "queue_manager blocks via runtime gate before SyncJob creation")
    require(push_guard < queue.find("assert_marketplace_push_allowed", push_guard) < syncjob, "queue_manager blocks via marketplace guard before SyncJob creation")

    syncjob_creators = []
    for path in ROOT.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "job = SyncJob(" in text:
            syncjob_creators.append(path.name)
    require(syncjob_creators == ["queue_manager.py"], f"only queue_manager creates SyncJob rows directly: {syncjob_creators}")


def assert_dispatcher(dispatcher: str) -> None:
    require("sync_item_to_store" not in dispatcher, "dispatcher no longer calls/imports sync_item_to_store")
    require("immediate_sync_store" not in dispatcher, "dispatcher does not call immediate_sync_store")
    require("execute_governed_push" in dispatcher and "smart_push_service" in dispatcher, "dispatcher calls smart_push_service for push jobs")
    require("sync_inventory_to_amazon(" not in dispatcher and "update_listing_quantity(" not in dispatcher, "dispatcher does not call marketplace APIs directly")
    require("runtime_status_writer" in dispatcher, "dispatcher uses centralized runtime status writer")


def assert_smart_push(smart: str) -> None:
    execute = function_block(smart, "execute_governed_push")
    require("is_runtime_action_allowed" in execute, "smart_push_service governed execution checks runtime gate")
    require("marketplace_push_eligibility" in execute, "smart_push_service governed execution checks marketplace eligibility")
    require("sync_inventory_to_amazon" in execute and "push_single_listing" in execute, "smart_push_service is marketplace execution layer")

    for legacy_name in ["push_to_store", "push_specific_sku"]:
        block = function_block(smart, legacy_name)
        require("[GOVERNED_PATH_ONLY]" in block and "sync_inventory_to_amazon" not in block and "update_listing_quantity" not in block, f"{legacy_name} is fail-closed")

    require("runtime_status_writer" in smart, "smart_push_service uses centralized runtime status writer for execution status")


def assert_legacy_services(sync_service: str) -> None:
    for legacy_name in ["sync_item_to_store", "automatic_push_to_stores", "push_quantity_to_ebay", "push_quantity_to_amazon"]:
        block = function_block(sync_service, legacy_name)
        require("[GOVERNED_PATH_ONLY]" in block, f"{legacy_name} is fail-closed")
        require("sync_inventory_to_amazon" not in block and "update_listing_quantity" not in block and "sync_inventory_to_ebay" not in block, f"{legacy_name} performs no marketplace call")


def assert_fba_fbm() -> None:
    from fba_fbm_helpers import marketplace_push_eligibility

    amazon = SimpleNamespace(id=1, name="Amazon FBM", platform="Amazon", fulfillment_type="FBM", fbm_sync_enabled=True, fba_import_enabled=False)
    ebay = SimpleNamespace(id=2, name="eBay", platform="eBay")
    stock = SimpleNamespace(sku="SKU-MFN", location="A1")

    def listing(channel):
        return SimpleNamespace(amazon_fulfillment_channel=channel)

    allowed, reason = marketplace_push_eligibility(amazon, sku="FBA-KA-OL-100-ML", listing=listing("MFN"), query_database=False)
    require(not allowed and "FBA-" in reason, "FBA SKU blocked")
    allowed, reason = marketplace_push_eligibility(amazon, sku="SKU-UNKNOWN", listing=listing(None), query_database=False)
    require(not allowed and "unknown/blank" in reason, "unknown Amazon fulfillment blocked")
    allowed, reason = marketplace_push_eligibility(amazon, sku="SKU-DEFAULT", listing=listing("DEFAULT"), query_database=False)
    require(not allowed and "FBA/read-only" in reason, "Amazon DEFAULT fulfillment blocked")
    allowed, reason = marketplace_push_eligibility(amazon, sku="SKU-BLANK", listing=listing(""), query_database=False)
    require(not allowed and "unknown/blank" in reason, "blank Amazon fulfillment blocked")
    allowed, reason = marketplace_push_eligibility(amazon, sku="SKU-MFN", warehouse_stock=stock, listing=listing("MFN"), query_database=False)
    require(allowed, "explicit Amazon MFN allowed")
    allowed, reason = marketplace_push_eligibility(amazon, sku="SKU-FBM", warehouse_stock=stock, listing=listing("FBM"), query_database=False)
    require(allowed, "explicit Amazon FBM allowed")
    allowed, reason = marketplace_push_eligibility(amazon, sku="SKU-MERCHANT", warehouse_stock=stock, listing=listing("MERCHANT"), query_database=False)
    require(allowed, "explicit Amazon MERCHANT allowed")
    allowed, reason = marketplace_push_eligibility(ebay, sku="SKU-EBAY", query_database=False)
    require(allowed, "eBay allowed by marketplace guard and route proof keeps it governed")


def main() -> int:
    print("BT38 Full Governed Execution Audit")
    print("Mode: static/in-memory proof only; no live marketplace API calls")
    print_execution_map()

    routes = read("routes.py")
    queue = read("queue_manager.py")
    dispatcher = read("sync_dispatcher.py")
    smart = read("smart_push_service.py")
    sync_service = read("sync_service.py")

    assert_routes(routes)
    assert_queue(queue)
    assert_dispatcher(dispatcher)
    assert_smart_push(smart)
    assert_legacy_services(sync_service)
    assert_fba_fbm()
    print("PASS: no live marketplace calls executed during audit")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FAIL: {exc}")
        raise SystemExit(1)
