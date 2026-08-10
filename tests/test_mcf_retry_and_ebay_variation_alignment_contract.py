from pathlib import Path

MAIN = Path("main.py").read_text(encoding="utf-8")
COMPAT = Path("services/governed_mcf_compat.py").read_text(encoding="utf-8")
MCF_EXEC = Path("services/governed_mcf_execution.py").read_text(encoding="utf-8")
FAILED_RETRY = Path("services/governed_failed_mcf_retry.py").read_text(encoding="utf-8")
VARIATION = Path("services/governed_ebay_variation_signal.py").read_text(encoding="utf-8")


def test_mcf_compatibility_binding_loads_before_startup_recovery():
    compat_pos = MAIN.index("import services.governed_mcf_compat")
    recovery_pos = MAIN.index("run_bounded_startup_recovery_alignment")
    assert compat_pos < recovery_pos
    assert "MCFService.submit_mcf_to_amazon = _submit" in COMPAT
    assert "return submit_mcf_order(mcf_order)" in COMPAT
    assert "FulfillmentOutbound" in MCF_EXEC
    assert "create_fulfillment_order" in MCF_EXEC


def test_failed_linked_mcf_retry_is_bounded_and_reuses_existing_authority():
    assert "limit: int = 10" in FAILED_RETRY
    assert "max_age_hours: int = 72" in FAILED_RETRY
    assert 'MCFOrder.status == "failed"' in FAILED_RETRY
    assert "MCFOrder.amazon_status.is_(None)" in FAILED_RETRY
    assert "MarketplaceOrder.shipped_at.is_(None)" in FAILED_RETRY
    assert "run_governed_mcf_submission(" in FAILED_RETRY
    assert "auto_release=True" in FAILED_RETRY
    assert "MCFOrder(" not in FAILED_RETRY
    assert "create_fulfillment_order(" not in FAILED_RETRY
    assert "new_worker_started" in FAILED_RETRY
    assert "new_queue_created" in FAILED_RETRY


def test_ambiguous_ebay_variation_resolves_exact_order_before_webhook_execution():
    enrich_pos = MAIN.index("enrich_ambiguous_ebay_order_signal(payload or {})")
    execute_pos = MAIN.index("result = original_webhook_execution(")
    assert enrich_pos < execute_pos
    assert "MarketplaceListing.external_listing_id == listing_id" in VARIATION
    assert "len(candidate_skus) <= 1" in VARIATION
    assert 'f"{EBAY_ORDERS_URL}/{quote(order_id' in VARIATION
    assert 'item.get("lineItemId")' in VARIATION
    assert 'item.get("sku")' in VARIATION
    assert 'payload["sku"] = exact_sku' in VARIATION
    assert "WarehouseStock" not in VARIATION
    assert "StockLedgerEntry" not in VARIATION


def test_ambiguous_variation_fails_closed_instead_of_guessing_sku():
    assert "ambiguous_ebay_variation_exact_order_read_failed" in VARIATION
    assert "ambiguous_ebay_variation_exact_sku_unresolved" in VARIATION
    assert "candidate_skus" in VARIATION
