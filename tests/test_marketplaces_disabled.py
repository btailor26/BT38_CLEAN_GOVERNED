"""
Proof tests for BT38 marketplace shutdown phase.

Goal:
- Amazon marketplace execution disabled
- eBay marketplace execution disabled
- No direct marketplace API execution remains active
- Compatibility imports still work without crashing the app
"""

import inspect

import amazon_service
import ebay_service


def test_amazon_marketplace_marked_disabled():
    assert amazon_service.MARKETPLACE_EXECUTION_DISABLED is True
    assert amazon_service.AMAZON_MARKETPLACE_DISABLED is True


def test_ebay_marketplace_marked_disabled():
    assert ebay_service.MARKETPLACE_EXECUTION_DISABLED is True
    assert ebay_service.EBAY_MARKETPLACE_DISABLED is True


def test_amazon_service_blocks_execution():
    svc = amazon_service.AmazonAPIService()

    ok, message = svc.sync_inventory_to_amazon()
    assert ok is False
    assert "disabled" in message.lower()

    result = svc.get_mfn_orders()
    assert result["execution_blocked"] is True
    assert result["marketplace_disabled"] is True


def test_ebay_service_blocks_execution():
    svc = ebay_service.eBayAPIService()

    ok, message = svc.push_quantity_only()
    assert ok is False
    assert "disabled" in message.lower()

    result = svc.get_item()
    assert result["execution_blocked"] is True
    assert result["marketplace_disabled"] is True


def test_amazon_source_contains_no_live_execution_markers():
    source = inspect.getsource(amazon_service)

    forbidden_markers = [
        "sp_api.api",
        "Feeds(",
        "Inventories(",
        "ListingsItems(",
        "requests.post",
        "requests.get",
        "create_feed",
        "submit_feed",
        "sync_inventory_to_amazon",
    ]

    for marker in forbidden_markers:
        assert marker not in source


def test_ebay_source_contains_no_live_execution_markers():
    source = inspect.getsource(ebay_service)

    forbidden_markers = [
        "requests.post",
        "requests.get",
        "api.ebay.com",
        "sandbox.ebay.com",
        "push_quantity_only(",
        "ReviseInventoryStatus",
        "GetItem",
    ]

    for marker in forbidden_markers:
        assert marker not in source
