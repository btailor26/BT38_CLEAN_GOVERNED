from pathlib import Path


SOURCE = Path(
    "services/governed_webhook_rejection_recovery.py"
).read_text(encoding="utf-8")


def _function_block(source: str, function_name: str) -> str:
    marker = f"def {function_name}("
    start = source.index(marker)
    next_function = source.find("\ndef ", start + len(marker))
    next_route = source.find("\n@app.", start + len(marker))
    candidates = [position for position in (next_function, next_route) if position != -1]
    end = min(candidates) if candidates else len(source)
    return source[start:end]


def test_manual_exact_ebay_recovery_route_is_exposed():
    assert '@app.post("/governed/actions/ebay/exact-order-recovery")' in SOURCE
    block = _function_block(SOURCE, "recover_exact_ebay_order_manually")
    assert "marketplace_order_id" in block
    assert "store_id" in block
    assert "hydrate_exact_ebay_order(" in block
    assert 'source="manual_exact_ebay_recovery"' in block


def test_manual_exact_ebay_recovery_is_existing_order_only():
    block = _function_block(SOURCE, "recover_exact_ebay_order_manually")
    assert "MarketplaceOrder.query" in block
    assert '"existing_marketplace_order_missing"' in block
    assert "active_ebay_store_not_found" in block


def test_manual_exact_ebay_recovery_requires_authentication():
    block = _function_block(SOURCE, "recover_exact_ebay_order_manually")
    assert "current_user" in block
    assert "TASK_API_KEY" in block
    assert "X-Task-Key" in block
    assert '"authentication_required"' in block


def test_manual_exact_ebay_recovery_does_not_start_parallel_authorities():
    block = _function_block(SOURCE, "recover_exact_ebay_order_manually")
    forbidden = (
        "run_governed_warehouse_sync",
        "run_governed_marketplace_order_import",
        "process_marketplace_notification",
        "process_exact_marketplace_order_line",
        "mutate_warehouse_stock_from_order_line",
        "run_governed_mcf_submission",
        "requests.post",
        "requests.put",
        "requests.patch",
        "requests.delete",
    )
    for term in forbidden:
        assert term not in block

    assert '"exact_order_only": True' in block
    assert '"broad_scan_started": False' in block
    assert '"order_replayed": False' in block
    assert '"stock_mutation_started": False' in block
    assert '"marketplace_write_started": False' in block


def test_manual_exact_ebay_recovery_returns_database_readback():
    block = _function_block(SOURCE, "recover_exact_ebay_order_manually")
    assert "db.session.expire_all()" in block
    assert '"database_readback": readback' in block
    assert '"status": row.status' in block
    assert '"shipped_at": row.shipped_at.isoformat() if row.shipped_at else None' in block
