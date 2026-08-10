from pathlib import Path
import ast


ROUTES = Path("governed_routes.py").read_text(encoding="utf-8")
RUNTIME = Path("services/governed_runtime_engine.py").read_text(encoding="utf-8")
CAPTURE = Path("services/governed_webhook_capture.py").read_text(encoding="utf-8")
EXECUTION = Path("services/governed_webhook_execution.py").read_text(encoding="utf-8")
MAIN = Path("main.py").read_text(encoding="utf-8")


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


def test_durable_capture_immediately_arms_exact_notification_as_processing():
    installer = _function_source(
        MAIN,
        "_install_governed_webhook_runtime_alignment",
    )

    capture_pos = installer.index("notification_record_id = capture_function(request)")
    processing_pos = installer.index('processing_status="PROCESSING"')
    context_pos = installer.index("g.bt38_notification_record_id")

    assert capture_pos < processing_pos < context_pos
    assert 'verification_status="PENDING"' in installer
    assert "parsed=True" in installer


def test_diagnostic_logging_cannot_be_execution_authority():
    installer = _function_source(
        MAIN,
        "_install_governed_webhook_runtime_alignment",
    )
    route = _function_source(ROUTES, "governed_marketplace_webhook_intake")

    assert "def _record_diagnostic_without_blocking" in installer
    assert "original_diagnostic(**kwargs)" in installer
    assert "except Exception as exc:" in installer
    assert "db.session.rollback()" in installer
    assert "SimpleNamespace(id=None" in installer

    # The original governed execution path remains the only commercial path.
    assert "process_marketplace_notification(" in route


def test_uncaught_post_capture_failure_is_terminal_failed_state():
    observer = _function_source(MAIN, "record_captured_webhook_failure")

    assert '"/governed/webhooks/ebay"' in observer
    assert '"/governed/webhooks/amazon"' in observer
    assert "bt38_notification_record_id" in observer
    assert 'processing_status="FAILED"' in observer
    assert 'last_error=str(exception)[:4000]' in observer
    assert "completed=True" in observer

    # Failure recording observes Flask teardown only; it does not replace the
    # existing webhook response/exception authority.
    assert "@app.errorhandler" not in MAIN
    assert "raise exception" not in observer
    assert "return jsonify" not in observer


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
    combined = "\n".join((ROUTES, RUNTIME, EXECUTION, MAIN))

    assert "DURABLE_RUNTIME_JOB_PATH_ENABLED = False" in RUNTIME
    assert "QUEUE_MANAGER_DISABLED" not in ROUTES
    assert "enqueue_sync_job(" not in ROUTES
    assert "start_worker(" not in ROUTES
    assert "Thread(" not in MAIN
    assert "Queue(" not in MAIN
