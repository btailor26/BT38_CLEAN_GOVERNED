from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_gunicorn_uses_one_process_for_process_memory_event_queue():
    source = _read("gunicorn.conf.py")
    assert "workers = 1" in source
    assert "threads = 4" in source


def test_exact_sale_hands_off_to_existing_mcf_path_immediately():
    source = _read("services/governed_order_stock_mutation.py")
    assert "def _attempt_immediate_mcf_handoff" in source
    assert "run_governed_mcf_submission(" in source
    assert "auto_release=True" in source
    assert "should_attempt_mcf = bool(is_sale(line) and not _is_return(line))" in source


def test_processed_sale_without_mcf_resumes_existing_handoff_without_stock_mutation():
    source = _read("services/governed_order_stock_mutation.py")
    assert 'reason": "already_processed_mcf_handoff_resumed"' in source
    assert '"stock_mutated": False' in source
    assert 'mcf_order_id <= 0' in source
    assert 'handoff = _attempt_immediate_mcf_handoff(line)' in source


def test_mcf_list_exposes_manual_repair_for_processed_ready_orders():
    template = _read("templates/mcf_orders.html")
    assert "Repair handoff" in template
    assert "row.state == 'ready' and not row.mcf and row.anchor.processed_at" in template
    assert "governed_mcf.send_order_to_mcf" in template
    assert 'name="auto_release"' in template
    assert 'value="1"' in template
    assert "Stock will not be applied again" in template


def test_live_runtime_remains_strict_event_only_without_startup_db_recovery_scan():
    gunicorn = _read("gunicorn.conf.py")
    event_runtime = _read("services/governed_event_runtime.py")
    assert "start_event_only_runtime" in gunicorn
    assert "no startup recovery or automatic hydration" in gunicorn
    assert "no startup MarketplaceOrder/FBA/MCF recovery scans" in event_runtime
    assert "_recover_mcf_auto_release_events(" not in event_runtime


def test_automatic_mcf_submission_arms_one_hour_dispatch_after_amazon_acceptance():
    source = _read("governed_mcf_routes.py")
    assert "accepted_at = mcf.amazon_status_updated_at or datetime.utcnow()" in source
    assert "dispatch_at = accepted_at + timedelta(hours=1)" in source
    assert '"phase": "dispatch"' in source
    assert 'line.status = "mcf_accepted_dispatch_pending"' in source
    assert "def run_governed_mcf_marketplace_dispatch" in source
    assert 'line.status = "mcf_dispatched_tracking_pending"' in source


def test_tracking_remains_later_enrichment_not_dispatch_gate():
    source = _read("governed_mcf_routes.py")
    assert "if mcf.tracking_number:" in source
    assert '"mcf_tracking_ebay_enrichment"' in source
    assert 'line.status = "mcf_tracking_updated"' in source
    assert "Amazon tracking was added to the existing eBay dispatch." in source


def test_runtime_engine_retains_explicit_recovery_helper_without_making_it_live_policy():
    source = _read("services/governed_runtime_engine.py")
    assert "def _recover_mcf_auto_release_events" in source
    assert 'phase = "dispatch"' in source
    assert 'phase = "submit"' in source
    event_runtime = _read("services/governed_event_runtime.py")
    assert "_recover_mcf_auto_release_events(" not in event_runtime


def test_overdue_explicit_recovery_dispatches_only_after_successful_amazon_submit():
    source = _read("services/governed_runtime_engine.py")
    submit_pos = source.index("result = run_governed_mcf_submission(")
    overdue_pos = source.index("overdue_recovery_dispatch =")
    dispatch_pos = source.index(
        "run_governed_mcf_marketplace_dispatch(",
        overdue_pos,
    )
    assert submit_pos < overdue_pos < dispatch_pos
    assert 'bool(payload.get("startup_recovered"))' in source
    assert "legacy_base + timedelta(hours=1)" in source


def test_marketplace_cancellation_is_persisted_then_sent_to_existing_amazon_mcf_client():
    webhook = _read("services/governed_webhook_execution.py")
    mcf_execution = _read("services/governed_mcf_execution.py")

    assert 'business_event == "cancellation"' in webhook
    assert 'line.status = "cancel_requested"' in webhook
    assert "from services.governed_mcf_execution import cancel_mcf_order" in webhook
    assert "def cancel_mcf_order" in mcf_execution
    assert ".cancel_fulfillment_order(" in mcf_execution
    assert 'mcf_order.amazon_status = "CANCELLED"' in mcf_execution


def test_fba_remains_amazon_controlled_during_cancellation_flow():
    webhook = _read("services/governed_webhook_execution.py")
    cancellation_start = webhook.index("def _handle_marketplace_cancellation")
    cancellation_end = webhook.index("def _parse_marketplace_order_timestamp")
    cancellation = webhook[cancellation_start:cancellation_end]
    assert "AmazonFBAInventory" not in cancellation
    assert "WarehouseStock" not in cancellation
    assert "apply_governed_amazon_fba_event" not in cancellation
