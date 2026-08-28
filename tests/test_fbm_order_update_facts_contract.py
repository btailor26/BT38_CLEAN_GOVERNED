from pathlib import Path


ALIGN = Path("services/fbm_marketplace_order_update_alignment.py").read_text(encoding="utf-8")
COMPAT = Path("services/governed_mcf_compat.py").read_text(encoding="utf-8")
STATE = Path("services/fbm_operational_state.py").read_text(encoding="utf-8")
AMAZON = Path("services/fbm_amazon_order_profile.py").read_text(encoding="utf-8")
EBAY = Path("services/governed_exact_ebay_order_hydration.py").read_text(encoding="utf-8")


def test_initial_fbm_page_remains_db_only_and_does_not_scan_marketplaces():
    assert "Initial /fbm rendering is deliberately DB-only" in STATE
    view = STATE.split("def fbm_view_state", 1)[1]
    assert "get_or_refresh_amazon_profile" not in view
    assert "hydrate_exact_ebay_order" not in view


def test_governed_order_update_refreshes_only_exact_touched_order_ids():
    assert "_collect_order_ids(result, order_ids)" in ALIGN
    assert "MarketplaceOrder.marketplace_order_id == order_id" in ALIGN
    assert "One marketplace read per order" in ALIGN
    assert "limit(300)" not in ALIGN
    assert "install_governed_order_update_alignment()" in ALIGN
    assert "fbm_marketplace_order_update_alignment" in COMPAT


def test_amazon_prime_and_promise_are_marketplace_owned_not_inferred():
    assert 'payload.get("IsPrime")' in AMAZON
    assert 'payload.get("EarliestDeliveryDate")' in AMAZON
    assert 'payload.get("LatestDeliveryDate")' in AMAZON
    assert "if raw_is_prime is not None" in AMAZON
    assert "get_or_refresh_amazon_profile(order, force=True)" in ALIGN


def test_ebay_promise_comes_from_exact_fulfillment_fields():
    assert 'facts.get("minEstimatedDeliveryDate")' in EBAY
    assert 'facts.get("maxEstimatedDeliveryDate")' in EBAY
    assert 'facts.get("shipByDate")' in EBAY
    assert "hydrate_exact_ebay_order" in ALIGN
