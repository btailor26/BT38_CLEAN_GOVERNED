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


def test_runtime_recovers_mcf_independently_of_retired_durable_job_path():
    source = _read("services/governed_runtime_engine.py")
    durable_pos = source.index("if DURABLE_RUNTIME_JOB_PATH_ENABLED and runtime_database_enabled:")
    recovery_pos = source.index("# MCF startup recovery is part of the active governed lifecycle")
    assert recovery_pos > durable_pos
    recovery_block = source[recovery_pos:]
    assert "if runtime_database_enabled:" in recovery_block
    assert "_recover_mcf_auto_release_events(" in recovery_block
    assert 'phase = "dispatch"' in source
    assert 'phase = "submit"' in source


def test_overdue_startup_recovery_dispatches_only_after_successful_amazon_submit():
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
