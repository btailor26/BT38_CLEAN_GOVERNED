from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ORDER_MUTATION = (ROOT / "services" / "governed_order_stock_mutation.py").read_text(encoding="utf-8")
WEBHOOK_EXECUTION = (ROOT / "services" / "governed_webhook_execution.py").read_text(encoding="utf-8")


def test_marketplace_pending_is_not_a_sale_or_stock_mutation_trigger():
    is_sale = ORDER_MUTATION.split("def is_sale", 1)[1].split("def _is_return", 1)[0]
    assert 'value == "pending"' not in is_sale
    mutation = ORDER_MUTATION.split("def mutate_warehouse_stock_from_order_line", 1)[1].split("def _attempt_immediate_mcf_handoff", 1)[0]
    assert 'if _line_type(line) == "pending"' in mutation
    assert '"reason": "marketplace_pending_not_actionable"' in mutation
    assert '"stock_mutated": False' in mutation
    assert '"mcf_handoff_started": False' in mutation


def test_resumed_mcf_failure_stays_diagnostic_not_marketplace_lifecycle():
    resumed = ORDER_MUTATION.split('"reason": "already_processed_mcf_handoff_resumed"', 1)[0].rsplit("return {", 1)[1]
    assert '"success": True' in resumed
    assert '"mcf_handoff_success": handoff_ok' in ORDER_MUTATION
    assert '"mcf_handoff": handoff' in ORDER_MUTATION


def test_webhook_still_imports_exact_marketplace_lifecycle_before_processing():
    assert 'status=lifecycle.get("status") or "pending"' in WEBHOOK_EXECUTION
    assert 'process_exact_marketplace_order_line(' in WEBHOOK_EXECUTION
