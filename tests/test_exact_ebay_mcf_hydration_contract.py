from pathlib import Path
import ast

HYDRATION = Path("services/governed_exact_ebay_order_hydration.py").read_text(encoding="utf-8")
STOCK = Path("services/governed_order_stock_mutation.py").read_text(encoding="utf-8")


def _function_source(source: str, name: str) -> str:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"Function not found: {name}")


def test_exact_ebay_hydration_is_bounded_to_existing_marketplace_order():
    fn = _function_source(HYDRATION, "hydrate_exact_ebay_order")
    assert "MarketplaceOrder.query" in fn
    assert "marketplace_order_id == order_id" in fn
    assert 'f"{EBAY_ORDERS_URL}/{quote(order_id' in fn
    assert "MarketplaceOrder(" not in fn
    assert "upsert_governed_marketplace_order_line" not in fn
    assert "mutate_warehouse_stock" not in fn
    assert "run_governed_mcf_submission" not in fn


def test_hydration_fills_mcf_delivery_fields_and_marketplace_timestamp():
    fn = _function_source(HYDRATION, "hydrate_exact_ebay_order")
    for field in (
        "ship_to_name",
        "ship_to_address",
        "ship_to_city",
        "ship_to_postcode",
        "ship_to_country",
    ):
        assert field in fn
    assert "creationDate" in fn
    assert "marketplace_created_at" in fn


def test_ebay_hydration_canonicalises_line_identity_and_idempotency_together():
    fn = _function_source(HYDRATION, "hydrate_exact_ebay_order")
    assert 'line_id = _text(item.get("lineItemId"))' in fn
    assert 'canonical_key = f"{store.id}:{order_id}:{line_id}:{_text(row.sku)}"' in fn
    assert "row.marketplace_order_item_id = line_id" in fn
    assert "row.idempotency_key = canonical_key" in fn
    assert "MarketplaceOrder.idempotency_key == canonical_key" in fn
    assert "exact_ebay_order_identity_conflict" in fn


def test_automatic_mcf_handoff_hydrates_before_existing_submission_authority():
    fn = _function_source(STOCK, "_attempt_immediate_mcf_handoff")
    hydrate_pos = fn.index("hydrate_exact_ebay_order(")
    submit_pos = fn.index("run_governed_mcf_submission(")
    assert hydrate_pos < submit_pos
    assert '"ebay" in platform and needs_delivery' in fn
    assert "automatic_mcf_exact_order_hydration_failed" in fn
    assert "auto_release=True" in fn
    assert "form_data={}" in fn


def test_no_manual_signal_or_parallel_mcf_builder_added():
    combined = HYDRATION + "\n" + STOCK
    assert "manual_mcf_submit" not in HYDRATION
    assert "MCFOrder(" not in HYDRATION
    assert "MCFService(" not in HYDRATION
    assert "run_governed_mcf_submission(row_id" in STOCK
