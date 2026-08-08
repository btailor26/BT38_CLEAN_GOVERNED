from pathlib import Path


ORDER_IMPORT = Path("services/governed_marketplace_order_import.py").read_text(encoding="utf-8")
WEBHOOK = Path("services/governed_webhook_execution.py").read_text(encoding="utf-8")
MCF_ROUTES = Path("governed_mcf_routes.py").read_text(encoding="utf-8")


def test_order_import_preserves_marketplace_time_and_bt38_arrival_time_separately():
    assert "marketplace_created_at: datetime | None = None" in ORDER_IMPORT
    assert "import_source: str | None = None" in ORDER_IMPORT
    assert "marketplace_created_at = COALESCE(:marketplace_created_at, marketplace_created_at)" in ORDER_IMPORT
    assert "order.created_at =" not in ORDER_IMPORT
    assert "order.updated_at = datetime.utcnow()" in ORDER_IMPORT


def test_ebay_and_amazon_copy_marketplace_order_time_into_canonical_upsert():
    assert 'order.get("creationDate")' in ORDER_IMPORT
    assert 'order.get("PurchaseDate")' in ORDER_IMPORT
    assert "marketplace_created_at=marketplace_created_at" in ORDER_IMPORT
    assert "import_source=source" in ORDER_IMPORT


def test_mcf_dispatch_clock_starts_only_after_amazon_acceptance():
    assert 'source="warehouse_mcf_one_hour_release"' not in ORDER_IMPORT
    assert "release_at = release_base + timedelta(hours=1)" not in ORDER_IMPORT
    assert "amazon_status_updated_at" in MCF_ROUTES
    assert "timedelta(hours=1)" in MCF_ROUTES


def test_webhook_hands_marketplace_time_and_source_to_same_canonical_upsert():
    assert "_parse_marketplace_order_timestamp(payload)" in WEBHOOK
    assert 'import_source=f"webhook_{marketplace}"' in WEBHOOK
    assert "upsert_governed_marketplace_order_line(" in WEBHOOK
    assert "MarketplaceOrder(" not in WEBHOOK
