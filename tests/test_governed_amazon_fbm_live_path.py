"""Stage 4 proof tests for the one governed Amazon FBM live path."""

from __future__ import annotations

from types import SimpleNamespace

import governed_execution
import queue_manager
import shutdown_http_guard
from services import runtime_gate


def approval_for(payload):
    return {
        "approved": True,
        "approval_type": "amazon_fbm_single_sku_inventory_push",
        "approved_by": "pytest",
        "approval_id": "approval-1",
        "source": "bt38_command_center",
        "scope": {
            "sku": payload["sku"],
            "store_id": payload["store_id"],
            "listing_id": payload["listing_id"],
            "quantity": payload["quantity"],
        },
    }


def live_payload(**overrides):
    payload = {
        "marketplace": "amazon",
        "action": "push_inventory",
        "sku": "FBM-SAFE-01",
        "store_id": 10,
        "listing_id": 20,
        "quantity": 7,
        "amazon_fulfillment_channel": "MFN",
        "marketplace_id": "A1F83G8C2ARO7P",
    }
    payload.update(overrides)
    return payload


def patch_valid_store_and_listing(monkeypatch, *, fulfillment="MFN", sku="FBM-SAFE-01"):
    store = SimpleNamespace(
        id=10,
        platform="Amazon",
        is_active=True,
        fbm_sync_enabled=True,
        fulfillment_type="FBM",
    )
    listing = SimpleNamespace(
        id=20,
        store_id=10,
        external_sku=sku,
        amazon_fulfillment_channel=fulfillment,
    )
    monkeypatch.setattr(governed_execution, "_resolve_store", lambda store_id: store)
    monkeypatch.setattr(governed_execution, "_resolve_listing", lambda listing_id: listing)
    return store, listing


def open_gate(monkeypatch):
    monkeypatch.setattr(runtime_gate, "RUNTIME_GATE_FORCE_CLOSED", False)


def close_gate(monkeypatch):
    monkeypatch.setattr(runtime_gate, "RUNTIME_GATE_FORCE_CLOSED", True)


def test_fba_live_push_blocked_before_adapter(monkeypatch):
    open_gate(monkeypatch)
    payload = live_payload(sku="FBA-CG-UN-05", amazon_fulfillment_channel="AFN")
    patch_valid_store_and_listing(monkeypatch, fulfillment="AFN", sku="FBA-CG-UN-05")

    def fail_adapter(_marketplace):
        raise AssertionError("FBA live push must be blocked before adapter selection")

    monkeypatch.setattr(governed_execution, "_select_adapter", fail_adapter)
    result = governed_execution.submit_governed_marketplace_action(
        payload,
        actor="pytest",
        approval=approval_for(payload),
        dry_run=False,
    )

    assert result["execution_blocked"] is True
    assert result["runtime_gate_allowed"] is True
    assert result["eligibility_checked"] is True
    assert "read-only" in result["reason"]


def test_unknown_fulfillment_live_push_blocked_before_adapter(monkeypatch):
    open_gate(monkeypatch)
    payload = live_payload(amazon_fulfillment_channel="")
    patch_valid_store_and_listing(monkeypatch, fulfillment="")
    monkeypatch.setattr(
        governed_execution,
        "_select_adapter",
        lambda _marketplace: (_ for _ in ()).throw(AssertionError("unknown fulfillment must block before adapter")),
    )

    result = governed_execution.submit_governed_marketplace_action(
        payload,
        actor="pytest",
        approval=approval_for(payload),
        dry_run=False,
    )

    assert result["execution_blocked"] is True
    assert result["runtime_gate_allowed"] is True
    assert "unknown" in result["reason"].lower()


def test_missing_approval_blocks_live_push(monkeypatch):
    open_gate(monkeypatch)
    payload = live_payload()
    patch_valid_store_and_listing(monkeypatch)

    result = governed_execution.submit_governed_marketplace_action(
        payload,
        actor="pytest",
        dry_run=False,
    )

    assert result["execution_blocked"] is True
    assert result["runtime_gate_checked"] is False
    assert "approval" in result["reason"].lower()


def test_runtime_gate_closed_blocks_live_push_before_adapter(monkeypatch):
    close_gate(monkeypatch)
    payload = live_payload()
    patch_valid_store_and_listing(monkeypatch)
    monkeypatch.setattr(
        governed_execution,
        "_select_adapter",
        lambda _marketplace: (_ for _ in ()).throw(AssertionError("closed gate must block before adapter")),
    )

    result = governed_execution.submit_governed_marketplace_action(
        payload,
        actor="pytest",
        approval=approval_for(payload),
        dry_run=False,
    )

    assert result["execution_blocked"] is True
    assert result["runtime_gate_checked"] is True
    assert result["runtime_gate_allowed"] is False
    assert "disabled" in result["reason"].lower()


def test_fbm_dry_run_still_no_live_call(monkeypatch):
    close_gate(monkeypatch)
    payload = live_payload()
    patch_valid_store_and_listing(monkeypatch)

    result = governed_execution.submit_governed_marketplace_action(
        payload,
        actor="pytest",
        approval=approval_for(payload),
        dry_run=True,
    )

    assert result["dry_run"] is True
    assert result["execution_blocked"] is True
    assert result["runtime_gate_allowed"] is False
    assert result["adapter"] == "amazon_fbm"
    assert "no live Listings API call" in result["reason"]


def test_fbm_live_call_only_reaches_amazon_adapter_when_gate_open(monkeypatch):
    open_gate(monkeypatch)
    payload = live_payload()
    patch_valid_store_and_listing(monkeypatch)
    calls = []

    class FakeAdapter:
        def execute(self, action, adapter_payload):
            calls.append((action, adapter_payload))
            return {
                "success": True,
                "ok": True,
                "governed": True,
                "dry_run": False,
                "execution_blocked": False,
                "marketplace": "amazon",
                "adapter": "amazon_fbm",
                "action": action,
                "reason": "fake adapter reached",
            }

    monkeypatch.setattr(governed_execution, "_select_adapter", lambda marketplace: FakeAdapter())

    result = governed_execution.submit_governed_marketplace_action(
        payload,
        actor="pytest",
        approval=approval_for(payload),
        dry_run=False,
    )

    assert result["ok"] is True
    assert result["runtime_gate_allowed"] is True
    assert result["eligibility_checked"] is True
    assert result["adapter"] == "amazon_fbm"
    assert calls and calls[0][0] == "push_inventory"
    assert calls[0][1]["_governed_dry_run"] is False
    assert calls[0][1]["_governed_store"].id == 10
    assert calls[0][1]["_governed_listing"].id == 20


def test_amazon_adapter_live_calls_single_governed_service_method(monkeypatch):
    from marketplace_adapters import amazon_fbm

    payload = live_payload(
        _governed_dry_run=False,
        _governed_store=SimpleNamespace(id=10),
        _governed_listing=SimpleNamespace(id=20),
        _governed_command_id="cmd-1",
        _governed_approval_id="approval-1",
    )
    calls = []

    class FakeAmazonService:
        def update_fbm_inventory_quantity_governed(self, **kwargs):
            calls.append(kwargs)
            return {"success": True, "ok": True, "service": "fake"}

    import amazon_service

    monkeypatch.setattr(amazon_service, "AmazonAPIService", FakeAmazonService)
    result = amazon_fbm.AmazonFbmAdapter().execute("push_inventory", payload)

    assert result["ok"] is True
    assert result["execution_blocked"] is False
    assert calls[0]["sku"] == "FBM-SAFE-01"
    assert calls[0]["quantity"] == 7
    assert calls[0]["fulfillment_channel"] == "MFN"


def test_route_still_dry_run_only_and_old_paths_remain_blocked():
    route_source = open("governed_routes.py", encoding="utf-8").read().split('@governed_bp.post("/governed/actions/sku/dry-run")', 1)[1]
    assert "submit_governed_marketplace_action" in route_source
    assert "dry_run=True" in route_source
    assert "AmazonAPIService" not in route_source
    assert "AmazonFbmAdapter" not in route_source
    assert shutdown_http_guard.is_shutdown_path("/api/push-sku") is True
    assert shutdown_http_guard.is_shutdown_path("/api/sync/amazon/sku/FBA-CG-UN-05") is True
    result = queue_manager.enqueue_sync_job(1, queue_manager.JOB_PUSH_ITEM, {"sku": "FBM-SAFE-01"})
    assert result["execution_blocked"] is True
