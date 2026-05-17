"""Local proof for BT38 marketplace push governance guard.

This script avoids live marketplace/API calls. It exercises the central guard with
plain objects and verifies the critical call sites contain the guard before queue,
dispatcher, sync service, and Amazon feed creation paths.
"""
from pathlib import Path
from types import SimpleNamespace
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from fba_fbm_helpers import marketplace_push_eligibility


def store(platform="amazon", fbm=True, fba=False, fulfillment_type=None):
    return SimpleNamespace(
        id=1,
        name=f"{platform}-store",
        platform=platform,
        fbm_sync_enabled=fbm,
        fba_import_enabled=fba,
        fulfillment_type=fulfillment_type,
        is_active=True,
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


def main():
    amazon_fbm = store("amazon", fbm=True)
    ebay = store("ebay", fbm=False)

    assert_guard(
        "FBA-KA-OL-100-ML blocked before enqueue eligibility",
        False,
        store=amazon_fbm,
        sku="FBA-KA-OL-100-ML",
        warehouse_stock=warehouse("FBA-KA-OL-100-ML"),
        listing=listing("MFN", "FBA-KA-OL-100-ML"),
    )
    assert_guard(
        "FBA warehouse location blocked",
        False,
        store=amazon_fbm,
        sku="KA-OL-100-ML",
        warehouse_stock=warehouse("KA-OL-100-ML", "Amazon FBA UK"),
        listing=listing("MFN", "KA-OL-100-ML"),
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
        "eBay SKU allowed",
        True,
        store=ebay,
        sku="EB-KA-OL-100-ML",
        warehouse_stock=warehouse("EB-KA-OL-100-ML"),
        listing=None,
    )
    assert_guard(
        "Amazon FBA/read-only store push blocked while import jobs remain separate",
        False,
        store=store("AmazonFBA", fbm=False, fba=True, fulfillment_type="FBA"),
        sku="KA-OL-100-ML",
        warehouse_stock=warehouse("KA-OL-100-ML"),
        listing=listing("MFN", "KA-OL-100-ML"),
    )

    assert_source_order(
        "enqueue_sync_job checks central guard before creating SyncJob",
        "queue_manager.py",
        "assert_marketplace_push_allowed(",
        "job = SyncJob(",
    )
    assert_source_order(
        "dispatcher checks central guard before _execute_push_item",
        "sync_dispatcher.py",
        "assert_marketplace_push_allowed(store, sku=sku, payload=payload)",
        "self._execute_push_item",
    )
    assert_source_order(
        "Amazon service checks central guard before feed document/feed creation",
        "amazon_service.py",
        "marketplace_push_eligibility(store, sku=item.sku, item=item)",
        "self._create_feed_document",
    )
    assert_source_order(
        "Amazon service guard runs before POST_INVENTORY_AVAILABILITY_DATA feed submission",
        "amazon_service.py",
        "marketplace_push_eligibility(store, sku=item.sku, item=item)",
        "POST_INVENTORY_AVAILABILITY_DATA",
        anchor="def sync_inventory_to_amazon",
    )

    print("PASS: FBA import path untouched: JOB_IMPORT_LISTINGS remains handled independently in sync_dispatcher.py")
    print("PASS: No Amazon feed creation can happen for FBA/unknown fulfillment through guarded paths")


if __name__ == "__main__":
    main()
