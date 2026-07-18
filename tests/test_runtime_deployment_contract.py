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
    return module


def test_idle_engine_loop_contains_no_database_polling():
    runtime = _runtime_module()
    tree = ast.parse(inspect.getsource(runtime._engine_loop))

    names = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
    }
    attributes = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
    }

    assert "SystemConfig" not in names
    assert "MarketplaceOrder" not in names
    assert "SystemLog" not in names
    assert "query" not in attributes
    assert "commit" not in attributes


def test_no_event_means_no_work_and_one_event_means_one_cycle(monkeypatch):
    runtime = _runtime_module()
    calls = []

    class FakeApp:
        def app_context(self):
            return contextlib.nullcontext()

    monkeypatch.setattr(runtime, "LIGHT_RECONCILE_SECONDS", 0.03)
    monkeypatch.setattr(runtime, "_acquire_runtime_owner_lock", lambda: True)
    monkeypatch.setattr(
        runtime,
        "_run_light_reconcile_cycle",
        lambda source="event": calls.append(source),
    )

    assert runtime.start_governed_runtime_engine(FakeApp()) is True
    time.sleep(0.09)
    assert calls == []

    runtime.notify_governed_runtime_work("deployment_contract")
    deadline = time.time() + 1.0
    while not calls and time.time() < deadline:
        time.sleep(0.01)

    assert calls == ["event_deployment_contract"]

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
