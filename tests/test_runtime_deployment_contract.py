import ast
import contextlib
import importlib
import inspect
import time


def _runtime_module():
    module = importlib.import_module("services.governed_runtime_engine")
    module._stop_event.set()
    module._pending_notification_event.set()
    time.sleep(0.02)
    module._stop_event.clear()
    module._pending_notification_event.clear()
    with module._pending_events_lock:
        module._pending_events.clear()
    module._started = False
    module._started_at = None
    module._runtime_lock_handle = None
    module._last_full_sync = None
    module._last_light_reconcile = None
    module._last_marketplace_import = None
    module._last_fba_import = None
    module._last_ebay_import = None
    module._last_error = None
    module._last_event_at = None
    module._last_event_source = None
    module._last_verification_result = None
    module._bootstrap_status = "not_started"
    module._bootstrap_started_at = None
    module._bootstrap_completed_at = None
    module._bootstrap_result = None
    return module


def test_idle_engine_loop_contains_no_database_polling():
    runtime = _runtime_module()
    tree = ast.parse(inspect.getsource(runtime._engine_loop))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    attributes = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }

    assert "SystemConfig" not in names
    assert "MarketplaceOrder" not in names
    assert "SystemLog" not in names
    assert "query" not in attributes
    assert "commit" not in attributes


def test_no_event_means_no_work_and_scoped_event_waits_for_15m_window(monkeypatch):
    runtime = _runtime_module()
    calls = []

    class FakeApp:
        def app_context(self):
            return contextlib.nullcontext()

    monkeypatch.setattr(runtime, "LIGHT_RECONCILE_SECONDS", 0.05)
    monkeypatch.setattr(runtime, "_acquire_runtime_owner_lock", lambda: True)
    monkeypatch.setattr(
        runtime,
        "_run_startup_marketplace_import",
        lambda app: {
            "success": True,
            "stores_attempted": 2,
            "stores_failed": 0,
        },
    )
    monkeypatch.setattr(
        runtime,
        "_run_light_reconcile_cycle",
        lambda events=None, source="verification": calls.append(list(events or [])),
    )

    assert runtime.start_governed_runtime_engine(FakeApp()) is True
    time.sleep(0.08)
    assert calls == []

    queued = runtime.notify_governed_runtime_work(
        "deployment_contract",
        store_id=23,
        marketplace="amazon",
        event_type="order",
        order_id="ORDER-123",
    )
    assert queued["scoped"] is True

    deadline = time.time() + 1.0
    while not calls and time.time() < deadline:
        time.sleep(0.01)

    assert len(calls) == 1
    assert calls[0][0]["store_id"] == 23
    assert calls[0][0]["order_id"] == "ORDER-123"

    runtime._stop_event.set()
    runtime._pending_notification_event.set()
    time.sleep(0.05)


def test_automatic_hydration_is_off_by_default(monkeypatch):
    runtime = _runtime_module()
    monkeypatch.delenv("ENABLE_GOVERNED_8H_HYDRATION", raising=False)
    status = runtime.get_governed_runtime_status()

    assert status["automatic_8h_hydration_enabled"] is False
    assert status["idle_db_activity"] is False
    assert status["runtime_status_source"] == "process_memory"

def test_runtime_starts_only_after_bootstrap_success(monkeypatch):
    runtime = _runtime_module()
    sequence = []

    class FakeApp:
        def app_context(self):
            return contextlib.nullcontext()

    monkeypatch.setattr(runtime, "_acquire_runtime_owner_lock", lambda: True)

    def successful_bootstrap(app):
        sequence.append("bootstrap")
        runtime._bootstrap_status = "completed"
        runtime._bootstrap_result = {
            "success": True,
            "stores_attempted": 2,
            "stores_failed": 0,
        }
        return runtime._bootstrap_result

    class FakeThread:
        def __init__(self, *, target, args, daemon, name):
            sequence.append("thread_created")
            self.target = target
            self.args = args

        def start(self):
            sequence.append("thread_started")

    monkeypatch.setattr(
        runtime,
        "_run_startup_marketplace_import",
        successful_bootstrap,
    )
    monkeypatch.setattr(runtime.threading, "Thread", FakeThread)

    assert runtime.start_governed_runtime_engine(FakeApp()) is True
    assert sequence == ["bootstrap", "thread_created", "thread_started"]
    assert runtime._started is True
    assert runtime._bootstrap_status == "completed"


def test_runtime_remains_paused_when_bootstrap_fails(monkeypatch):
    runtime = _runtime_module()
    thread_started = []

    class FakeApp:
        def app_context(self):
            return contextlib.nullcontext()

    monkeypatch.setattr(runtime, "_acquire_runtime_owner_lock", lambda: True)

    def failed_bootstrap(app):
        runtime._bootstrap_status = "failed"
        runtime._bootstrap_result = {
            "success": False,
            "stores_attempted": 2,
            "stores_failed": 1,
        }
        raise RuntimeError("startup_marketplace_import_failed:1")

    class ForbiddenThread:
        def __init__(self, *args, **kwargs):
            thread_started.append(True)

    monkeypatch.setattr(
        runtime,
        "_run_startup_marketplace_import",
        failed_bootstrap,
    )
    monkeypatch.setattr(runtime.threading, "Thread", ForbiddenThread)

    assert runtime.start_governed_runtime_engine(FakeApp()) is False
    assert runtime._started is False
    assert runtime._started_at is None
    assert runtime._bootstrap_status == "failed"
    assert thread_started == []


def test_runtime_status_exposes_bootstrap_gate():
    runtime = _runtime_module()
    runtime._bootstrap_status = "completed"
    runtime._bootstrap_result = {
        "success": True,
        "stores_attempted": 2,
        "stores_failed": 0,
    }

    status = runtime.get_governed_runtime_status()

    assert status["bootstrap_status"] == "completed"
    assert status["bootstrap_ready"] is True
    assert status["bootstrap_result"]["stores_failed"] == 0
