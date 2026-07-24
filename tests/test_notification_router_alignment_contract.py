from pathlib import Path


WEBHOOK = Path(
    "services/governed_webhook_execution.py"
).read_text(encoding="utf-8")

ROUTER = Path(
    "services/governed_notification_router.py"
).read_text(encoding="utf-8")

EBAY_HANDLER = Path(
    "services/governed_ebay_notification.py"
).read_text(encoding="utf-8")

IMPORTER = Path(
    "services/governed_marketplace_order_import.py"
).read_text(encoding="utf-8")


def test_generic_webhook_execution_uses_notification_router():
    assert "route_marketplace_notification" in WEBHOOK


def test_generic_webhook_execution_does_not_call_ebay_importer():
    assert "run_governed_ebay_exact_order_import" not in WEBHOOK
    assert "_run_ebay_order_import" not in WEBHOOK


def test_notification_router_does_not_execute_orders_or_stock():
    forbidden = (
        "MarketplaceOrder(",
        "mutate_warehouse_stock",
        "push_group_listings",
        "push_marketplace_listing",
        "create_mcf",
    )

    for token in forbidden:
        assert token not in ROUTER


def test_ebay_handler_owns_notification_interpretation():
    assert "_extract_ebay_order_id" in EBAY_HANDLER
    assert "handle_governed_ebay_notification" in EBAY_HANDLER


def test_ebay_handler_enters_exact_governed_importer():
    assert "run_governed_ebay_exact_order_import" in EBAY_HANDLER


def test_ebay_handler_does_not_create_order_or_mutate_stock_directly():
    forbidden = (
        "MarketplaceOrder(",
        "mutate_warehouse_stock",
        "push_group_listings",
        "push_marketplace_listing",
    )

    for token in forbidden:
        assert token not in EBAY_HANDLER


def test_exact_importer_remains_single_order_authority():
    assert "def run_governed_ebay_exact_order_import(" in IMPORTER
    assert "def _run_ebay_order_import(" in IMPORTER
    assert "process_exact_marketplace_order_line(" in IMPORTER
