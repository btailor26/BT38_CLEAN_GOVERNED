from pathlib import Path


def test_bell_projection_is_final_notification_owner():
    source = Path("main.py").read_text(encoding="utf-8")

    exact = source.index("install_governed_exact_record_event_alignment(app)")
    bell = source.index("install_governed_bell_event_projection_alignment(app)", exact)
    tail = source[bell:]

    assert "install_governed_notification_read_alignment(app)" not in tail
    assert "zero-query against Neon" in source


def test_webhook_native_order_change_can_project_sale_without_db_read():
    source = Path("services/governed_bell_event_projection_alignment.py").read_text(
        encoding="utf-8"
    )

    assert 'source in {"webhook_amazon", "webhook_ebay"}' in source
    assert "and order_id and sku" in source
    assert 'return "Sale"' in source

    # Browser projection must apply the same rule to the exact event already
    # observed in-session; it must not recover a missed sale with a DB query.
    assert "source==='webhook_amazon'||source==='webhook_ebay'" in source
    assert "&&orderId&&sku)return 'Sale'" in source
    assert "window.addEventListener('bt38-marketplace-event'" in source


def test_bell_reader_remains_zero_query_projection():
    source = Path("services/governed_bell_event_projection_alignment.py").read_text(
        encoding="utf-8"
    )

    assert "ready._event_only_bell_reader" in source
    assert "db.session" not in source
    assert "MarketplaceOrder.query" not in source
    assert "FBMShipment.query" not in source
