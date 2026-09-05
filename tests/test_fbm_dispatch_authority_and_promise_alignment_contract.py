from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DISPATCH = (
    ROOT / "services" / "governed_fbm_marketplace_dispatch_authority_alignment.py"
).read_text(encoding="utf-8")
BELL = (
    ROOT / "services" / "governed_fbm_ready_landing_alignment.py"
).read_text(encoding="utf-8")
EBAY = (
    ROOT / "services" / "governed_exact_ebay_order_hydration.py"
).read_text(encoding="utf-8")


def test_real_persisted_shipment_wins_over_marketplace_dispatch_proxy():
    assert "if key in result and result[key] is not None:" in DISPATCH
    assert "continue\n            marketplace = _marketplace_shipment(row)" in DISPATCH
    assert "result[key] = marketplace" in DISPATCH
    assert "page._workspace_shipping_mode = aligned_shipping_mode" not in DISPATCH
    assert "page._workspace_provider_options = aligned_provider_options" not in DISPATCH


def test_pending_marketplace_orders_are_not_bell_dispatch_actions():
    assert 'actionable_statuses = ("unshipped", "confirmed", "partially_shipped")' in BELL
    assert '"confirmed", "unshipped", "pending"' not in BELL
    assert '"marketplace_sale", "sale", "order_received", "new_order", "confirmed", "unshipped"' in BELL


def test_shipment_journey_bell_keeps_product_context():
    assert "MarketplaceOrder.sku" in BELL
    assert "MarketplaceOrder.quantity" in BELL
    assert "WarehouseStock.product_name" in BELL
    assert '"title": f"{label} · {row.platform or \'Marketplace\'} · {product_title}"' in BELL
    assert '"product_title": product_title' in BELL
    assert '"quantity": quantity' in BELL


def test_exact_ebay_get_order_persists_marketplace_deadlines_without_guessing():
    assert 'value.get("shipByDate")' in EBAY
    assert 'value.get("minEstimatedDeliveryDate")' in EBAY
    assert 'value.get("maxEstimatedDeliveryDate")' in EBAY
    assert "def _single_exact_value(values" in EBAY
    assert 'source="exact_ebay_order_hydration"' in EBAY
    assert "INSERT INTO fbm_order_operational_state" in EBAY
    assert "ship_by_at = COALESCE(EXCLUDED.ship_by_at" in EBAY
    assert '"promise_persisted": promise_persisted' in EBAY


def test_alignment_adds_no_polling_replay_or_marketplace_write():
    combined = (DISPATCH + BELL + EBAY).lower()
    assert "setinterval(" not in combined
    assert "threading.thread" not in combined
    assert "marketplace_write_started\": true" not in combined
    assert "backfill" not in DISPATCH.lower()
    assert "backfill" not in EBAY.lower()
