from pathlib import Path
import ast


ROUTES = Path("governed_routes.py").read_text(encoding="utf-8")
RUNTIME = Path("services/governed_runtime_engine.py").read_text(encoding="utf-8")
CAPTURE = Path("services/governed_webhook_capture.py").read_text(encoding="utf-8")
EXECUTION = Path("services/governed_webhook_execution.py").read_text(encoding="utf-8")


def _function_source(source: str, name: str) -> str:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"Function not found: {name}")


def test_marketplace_events_use_existing_single_governed_path():
    route = _function_source(ROUTES, "governed_marketplace_webhook_intake")

    assert "capture_ebay_notification(request)" in route
    assert "capture_amazon_notification(request)" in route
    assert "process_marketplace_notification(" in route
    assert "run_governed_marketplace_order_import" not in route
    assert "run_governed_marketplace_import_refresh" not in route
    assert "MarketplaceOrder(" not in route


def test_durable_capture_is_first_and_processing_state_is_armed_before_diagnostics():
    route = _function_source(ROUTES, "governed_marketplace_webhook_intake")

    capture_pos = min(
        route.index("capture_ebay_notification(request)"),
        route.index("capture_amazon_notification(request)"),
    )
    processing_pos = route.index('processing_status="PROCESSING"')
    diagnostics_pos = route.index("_bt38_record_webhook_event(")
    execution_pos = route.index("process_marketplace_notification(")

    assert capture_pos < processing_pos < diagnostics_pos < execution_pos


def test_diagnostic_logging_cannot_be_execution_authority():
    route = _function_source(ROUTES, "governed_marketplace_webhook_intake")

    assert "system_log_id" in route
    assert "process_marketplace_notification(" in route

    diagnostics_pos = route.index("_bt38_record_webhook_event(")
    execution_pos = route.index("process_marketplace_notification(")
    assert diagnostics_pos < execution_pos

    # Diagnostic logging may provide evidence, but a logging failure must not
    # be able to strand a durably captured commercial event in RECEIVED.
    diagnostics_section = route[diagnostics_pos:execution_pos]
    assert "try:" in diagnostics_section
    assert "except Exception" in diagnostics_section


def test_allowed_event_has_only_terminal_processing_outcomes():
    route = _function_source(ROUTES, "governed_marketplace_webhook_intake")

    assert 'processing_status="PROCESSING"' in route
    assert 'processing_status="COMPLETED"' in route
    assert 'processing_status="FAILED"' in route
    assert 'last_error=str(exc)[:4000]' in route

    # RECEIVED is capture state only. Once execution is allowed, the route must
    # not intentionally return while leaving the event in RECEIVED.
    allowed_start = route.index('status = "received" if allowed else "blocked_by_fuse"')
    allowed_section = route[allowed_start:]
    assert 'processing_status="RECEIVED"' not in allowed_section


def test_fuse_block_is_explicit_terminal_state_not_silent_sleep():
    route = _function_source(ROUTES, "governed_marketplace_webhook_intake")

    assert "if not allowed:" in route
    assert 'processing_status="BLOCKED"' in route
    assert "parsed=True" in route
    assert "completed=True" in route


def test_runtime_is_event_driven_24x7_not_database_polling():
    engine = _function_source(RUNTIME, "_engine_loop")
    notify = _function_source(RUNTIME, "notify_governed_runtime_work")

    assert "_pending_notification_event.wait(" in engine
    assert "_pop_due_events()" in engine
    assert "if due_hints:" in engine
    assert "_run_light_reconcile_cycle(" in engine

    assert "_pending_events.append(item)" in notify
    assert "_pending_notification_event.set()" in notify

    # Normal idle runtime must not restore broad order/catalogue scans.
    assert "MarketplaceOrder.query.all" not in engine
    assert "WarehouseStock.query.all" not in engine
    assert "MarketplaceListing.query.all" not in engine


def test_capture_and_execution_remain_separate_existing_responsibilities():
    capture = _function_source(CAPTURE, "capture_ebay_notification")
    execution = _function_source(EXECUTION, "process_marketplace_notification")

    assert "INSERT INTO webhooks.ebay_notifications" in capture
    assert "process_marketplace_notification" not in capture

    assert "upsert_governed_marketplace_order_line" in EXECUTION
    assert "capture_ebay_notification" not in execution


def test_24x7_contract_does_not_create_parallel_worker_or_queue():
    combined = "\n".join((ROUTES, RUNTIME, EXECUTION))

    assert "DURABLE_RUNTIME_JOB_PATH_ENABLED = False" in RUNTIME
    assert "QUEUE_MANAGER_DISABLED" not in ROUTES
    assert "enqueue_sync_job(" not in ROUTES
    assert "start_worker(" not in ROUTES
