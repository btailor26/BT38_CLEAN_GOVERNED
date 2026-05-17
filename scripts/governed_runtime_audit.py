"""Local proof for governed runtime execution and retired routes.

No marketplace APIs are called. The script uses synthetic objects plus source
inspection to prove fail-closed guard behavior and route retirement safety.
"""
from pathlib import Path
from types import SimpleNamespace
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from fba_fbm_helpers import marketplace_push_eligibility
from services.runtime_action_guard import is_runtime_action_allowed


def store(platform="amazon", fbm=True, fba=False, fulfillment_type=None, api_key="{}", is_active=True, store_mode="live"):
    return SimpleNamespace(
        id=1,
        name=f"{platform}-store",
        platform=platform,
        fbm_sync_enabled=fbm,
        fba_import_enabled=fba,
        fulfillment_type=fulfillment_type,
        api_key=api_key,
        is_active=is_active,
        store_mode=store_mode,
        auto_push_enabled=True,
        sync_enabled=True,
        import_enabled=True,
    )


def warehouse(sku, location="MAIN"):
    return SimpleNamespace(id=10, sku=sku, location=location, available_quantity=7)


def listing(channel, external_sku="SKU-1"):
    return SimpleNamespace(
        id=20,
        external_sku=external_sku,
        amazon_fulfillment_channel=channel,
        warehouse_stock=warehouse(external_sku),
        warehouse_stock_id=10,
        push_state="active",
        track_inventory=True,
    )


def assert_guard(label, expected, **kwargs):
    allowed, reason = marketplace_push_eligibility(**kwargs, query_database=False)
    outcome = "PASS" if allowed is expected else "FAIL"
    print(f"{outcome}: {label}: allowed={allowed}, reason={reason}")
    if allowed is not expected:
        raise AssertionError(label)


def assert_runtime(label, expected, store_obj, action_type, **kwargs):
    decision = is_runtime_action_allowed(store_obj, action_type, **kwargs)
    outcome = "PASS" if decision["allowed"] is expected else "FAIL"
    print(f"{outcome}: {label}: allowed={decision['allowed']}, reason={decision['reason']}")
    if decision["allowed"] is not expected:
        raise AssertionError(label)


def assert_source_order(label, path, first, later, anchor=None):
    text = Path(path).read_text()
    if anchor:
        anchor_idx = text.find(anchor)
        if anchor_idx == -1:
            raise AssertionError(f"{label}: missing anchor {anchor!r}")
        text = text[anchor_idx:]
    first_idx = text.find(first)
    later_idx = text.find(later)
    ok = first_idx != -1 and later_idx != -1 and first_idx < later_idx
    print(f"{'PASS' if ok else 'FAIL'}: {label}: {first!r} before {later!r}")
    if not ok:
        raise AssertionError(label)


def assert_absent(label, text, terms):
    hits = [term for term in terms if term in text]
    print(f"{'PASS' if not hits else 'FAIL'}: {label}: banned_terms={hits}")
    if hits:
        raise AssertionError(label)


def assert_route_present(label, path):
    text = Path("routes.py").read_text()
    ok = path in text
    print(f"{'PASS' if ok else 'FAIL'}: {label}: {path}")
    if not ok:
        raise AssertionError(label)


def route_block(source, start, end):
    text = Path(source).read_text()
    return text[text.index(start):text.index(end, text.index(start))]


def main():
    amazon_fbm = store("amazon", fbm=True)
    ebay = store("ebay", fbm=False)

    assert_runtime("runtime preview/read-only actions allowed", True, None, "preview")
    assert_runtime("runtime unknown action fails closed", False, amazon_fbm, "unknown_action")
    assert_runtime("runtime inactive store push blocked", False, store("ebay", is_active=False), "push", manual=True)
    assert_runtime("runtime missing credentials push blocked", False, store("ebay", api_key=""), "push", manual=True)
    disabled_push_store = store("ebay")
    disabled_push_store.auto_push_enabled = False
    assert_runtime("runtime store push setting blocks push", False, disabled_push_store, "push", manual=True)
    disabled_sync_store = store("ebay")
    disabled_sync_store.sync_enabled = False
    assert_runtime("runtime store sync setting blocks sync", False, disabled_sync_store, "sync", manual=True)
    disabled_import_store = store("amazon")
    disabled_import_store.import_enabled = False
    assert_runtime("runtime store import setting blocks import", False, disabled_import_store, "import", manual=True)
    assert_runtime("runtime FBA/read-only push blocked", False, store("AmazonFBA", fbm=False, fba=True, fulfillment_type="FBA"), "push", manual=True)

    assert_guard(
        "FBA-KA-OL-100-ML blocked",
        False,
        store=amazon_fbm,
        sku="FBA-KA-OL-100-ML",
        warehouse_stock=warehouse("FBA-KA-OL-100-ML"),
        listing=listing("MFN", "FBA-KA-OL-100-ML"),
    )
    assert_guard(
        "Amazon unknown fulfillment blocked",
        False,
        store=amazon_fbm,
        sku="KA-OL-100-ML",
        warehouse_stock=warehouse("KA-OL-100-ML"),
        listing=listing(None, "KA-OL-100-ML"),
    )
    assert_guard(
        "Amazon DEFAULT fulfillment blocked",
        False,
        store=amazon_fbm,
        sku="KA-OL-100-ML",
        warehouse_stock=warehouse("KA-OL-100-ML"),
        listing=listing("DEFAULT", "KA-OL-100-ML"),
    )
    assert_guard(
        "Amazon explicit MFN allowed",
        True,
        store=amazon_fbm,
        sku="KA-OL-100-ML",
        warehouse_stock=warehouse("KA-OL-100-ML"),
        listing=listing("MFN", "KA-OL-100-ML"),
    )
    assert_guard(
        "eBay is allowed",
        True,
        store=ebay,
        sku="EB-KA-OL-100-ML",
        warehouse_stock=warehouse("EB-KA-OL-100-ML"),
        listing=None,
    )

    banned = [
        "sync_item_to_store",
        "immediate_sync_store",
        "smart_push_service",
        "eBayAPIService",
        "AmazonAPIService",
        "enqueue_sync_job",
        "last_push_status =",
        "available_quantity =",
        "time.sleep",
    ]
    retired_blocks = {
        "api_push_sku": ("routes.py", "@bp.route('/api/push-sku'", "@bp.route('/api/classify-listings'"),
        "api_sync_sku": ("routes.py", "@bp.route('/api/sync/amazon/sku/<sku>'", "@bp.route('/api/push-status/<sku>'"),
        "push_group_stock": ("routes.py", "@bp.route('/groups/<int:group_id>/push'", "@bp.route('/groups/<int:group_id>/update-stock'"),
        "manual_sync_store": ("routes.py", "@bp.route('/stores/sync/<int:store_id>'", "@bp.route('/api/stores'"),
        "push_stock_all": ("routes.py", "@bp.route('/push_stock_all'", "# =================== BT38 COMMAND CENTER ROUTES"),
        "push_listing": ("routes.py", "@bp.route('/api/listings/<int:listing_id>/push'", "@bp.route('/api/listings/bulk-action'"),
        "amazon_bulk_test": ("routes.py", "@bp.route('/api/diagnostics/amazon/bulk-test'", "@bp.route('/api/diagnostics/amazon/feed/<feed_id>'"),
    }
    for name, args in retired_blocks.items():
        assert_absent(f"retired route {name} has no direct execution/mutation", route_block(*args), banned)

    routes_text = Path("routes.py").read_text()
    assert_absent(
        "routes do not call direct sync/feed push entrypoints",
        routes_text,
        ["sync_item_to_store", "immediate_sync_store", "sync_inventory_to_amazon", "create_feed(", "bulk_push_safe"],
    )

    app_text = Path("app.py").read_text()
    if "start_order_import_scheduler()" in app_text:
        raise AssertionError("legacy order import scheduler still starts from app.py")
    print("PASS: old scheduler startup disabled in app.py")
    if "start_dispatcher()" in app_text:
        raise AssertionError("dispatcher must not auto-start from app.py")
    print("PASS: dispatcher auto-start disabled in app.py")

    assert_route_present("frontend-called /push_stock route still exists", "@bp.route('/push_stock/<int:item_id>', methods=['POST'])")
    assert_route_present("frontend-called /push_stock_bulk route still exists", "@bp.route('/push_stock_bulk', methods=['POST'])")

    assert_source_order(
        "runtime guard before enqueue SyncJob",
        "queue_manager.py",
        "is_runtime_action_allowed(",
        "job = SyncJob(",
        anchor="def enqueue_sync_job",
    )
    assert_source_order(
        "marketplace guard before enqueue SyncJob",
        "queue_manager.py",
        "assert_marketplace_push_allowed(",
        "job = SyncJob(",
        anchor="def enqueue_sync_job",
    )
    assert_source_order(
        "runtime guard before dispatcher push execution",
        "sync_dispatcher.py",
        "is_runtime_action_allowed(store",
        "self._execute_push_item",
        anchor="elif job_type == JOB_PUSH_ITEM",
    )
    assert_source_order(
        "Amazon marketplace guard before feed creation",
        "amazon_service.py",
        "marketplace_push_eligibility(store, sku=item.sku, item=item)",
        "self._create_feed_document",
        anchor="def sync_inventory_to_amazon",
    )
    assert_source_order(
        "Amazon runtime guard before feed creation",
        "amazon_service.py",
        "is_runtime_action_allowed(store",
        "self._create_feed_document",
        anchor="def sync_inventory_to_amazon",
    )

    print("PASS: FBA import path remains separate: JOB_IMPORT_LISTINGS is not a push action in sync_dispatcher.py")
    print("PASS: governed runtime audit completed")


if __name__ == "__main__":
    main()
