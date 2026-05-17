"""
BT38 old sync shutdown proof test.

This test is intentionally strict. It proves the shutdown is NOT complete unless
all old marketplace execution surfaces are either removed or explicitly retired.

Required before adding the new governed path:
- no old worker loops
- no old queue job creation
- no direct Amazon/eBay execution
- no route-level direct push/sync/import execution
- no debug/test marketplace routes left active
- no credential-fix/import/normalization route that can mutate marketplace setup outside the new path
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def assert_not_contains(path: str, forbidden: list[str]):
    source = read(path)
    missing = []
    for marker in forbidden:
        if marker in source:
            missing.append(marker)
    assert not missing, f"{path} still contains forbidden old-sync markers: {missing}"


def test_worker_and_scheduler_startup_are_disabled():
    source = read("sync_dispatcher.py")
    required = [
        "WORKERS_DISABLED",
        "start_dispatcher() blocked",
        "start_order_import_scheduler() blocked",
    ]
    for marker in required:
        assert marker in source

    forbidden = [
        "threading.Thread",
        "while self.running",
        "_dispatcher_loop",
        "_process_store_queue",
        "_execute_job",
        "OrderImportScheduler",
        "SyncDispatcher",
    ]
    for marker in forbidden:
        assert marker not in source


def test_queue_manager_cannot_create_or_process_jobs():
    source = read("queue_manager.py")
    assert "QUEUE_MANAGER_DISABLED" in source
    assert "LEGACY_SYNC_QUEUE_DISABLED" in source
    assert "Fail closed" in source

    forbidden = [
        "SyncJob(",
        "db.session.add(job)",
        "status='pending'",
        "status = 'running'",
        "status = 'completed'",
        "retry_count",
    ]
    for marker in forbidden:
        assert marker not in source


def test_marketplace_services_have_no_live_api_markers():
    assert_not_contains("amazon_service.py", [
        "from sp_api",
        "sp_api.api",
        "Feeds(",
        "Inventories(",
        "ListingsItems(",
        "requests.get",
        "requests.post",
        "create_feed",
        "submit_feed",
    ])
    assert_not_contains("ebay_service.py", [
        "requests.get",
        "requests.post",
        "api.ebay.com",
        "sandbox.ebay.com",
        "ReviseInventoryStatus",
        "GetItem",
        "GetMyeBay",
        "Trading API",
    ])


def test_old_orchestration_services_are_disabled():
    required_pairs = {
        "sync_service.py": ["SYNC_SERVICE_DISABLED", "LEGACY_SYNC_ORCHESTRATION_DISABLED"],
        "smart_push_service.py": ["SMART_PUSH_DISABLED", "LEGACY_PUSH_ORCHESTRATION_DISABLED"],
        "auto_push_service.py": ["AUTO_PUSH_SERVICE_DISABLED", "LEGACY_AUTO_PUSH_DISABLED"],
        "warehouse_push_coordinator.py": ["WAREHOUSE_PUSH_COORDINATOR_DISABLED", "LEGACY_WAREHOUSE_PUSH_DISABLED"],
        "marketplace_order_processor.py": ["MARKETPLACE_ORDER_PROCESSOR_DISABLED", "LEGACY_ORDER_IMPORT_DISABLED"],
    }
    for path, markers in required_pairs.items():
        source = read(path)
        for marker in markers:
            assert marker in source, f"{path} missing disabled marker {marker}"


def test_runtime_gate_is_force_closed():
    source = read("services/runtime_gate.py")
    assert "RUNTIME_GATE_FORCE_CLOSED = True" in source
    assert "return False" in source
    assert "BT38 marketplace push/sync/import is disabled" in source


def test_routes_do_not_expose_old_execution_or_mutation_paths():
    source = read("routes.py")

    # These route decorators must not remain active unless the handler body is explicitly retired.
    forbidden_active_route_patterns = [
        "@bp.get(\"/api/diagnostics/ebay/health\")",
        "@bp.get(\"/api/diagnostics/ebay/policies\")",
        "@bp.get(\"/api/diagnostics/ebay/raw-import\")",
        "@bp.post(\"/api/admin/fix-sandbox-flag\")",
        "@bp.post(\"/api/admin/ebay/normalize-itemids\")",
        "@bp.route('/ebay-setup'",
        "@bp.route('/test-ebay-connection'",
    ]
    for marker in forbidden_active_route_patterns:
        assert marker not in source, f"Route still active and must be retired or blocked: {marker}"

    # Direct execution calls must not appear in routes.py.
    forbidden_execution_calls = [
        "enqueue_sync_job(",
        "smart_push_service.push_specific_sku",
        "sync_inventory_to_amazon",
        "sync_inventory_to_ebay",
        "import_inventory_from_ebay",
        "import_inventory_from_amazon",
        "authenticate_store(store)",
        "resolve_item_id_by_sku",
        "get_seller_profiles",
        "get_ebay_official_time",
    ]
    for marker in forbidden_execution_calls:
        assert marker not in source, f"routes.py still contains old execution call: {marker}"


def test_app_level_debug_and_startup_paths_are_not_active():
    source = read("app.py")

    forbidden_app_routes = [
        "@app.route(\"/debug/fba-local\")",
        "@app.route(\"/debug/fba-local-direct\")",
        "@app.route(\"/debug/fba-open\")",
    ]
    for marker in forbidden_app_routes:
        assert marker not in source, f"Debug route still active and must be removed/retired: {marker}"

    # Startup must not claim live dispatcher/scheduler started during shutdown mode.
    forbidden_startup_claims = [
        "Sync dispatcher started",
        "Order import scheduler started",
        "dispatcher is the single execution path",
    ]
    for marker in forbidden_startup_claims:
        assert marker not in source, f"Misleading startup claim remains: {marker}"
