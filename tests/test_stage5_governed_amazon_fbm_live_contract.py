"""Stage 5 contract tests for the one manual governed Amazon FBM live path.

These tests define the future live contract without enabling production live
execution. The default runtime remains closed; tests explicitly monkeypatch both
Stage 5 gates before proving the internal-only happy path.
"""

from __future__ import annotations

from types import SimpleNamespace

import governed_execution
import queue_manager
import shutdown_http_guard
import sync_dispatcher
from services import runtime_gate


def live_payload(**overrides):
    payload = {
        "marketplace": "amazon",
        "action": "push_inventory",
        "sku": "FBM-STAGE5-01",
        "store_id": 101,
        "listing_id": 202,
        "quantity": 9,
        "amazon_fulfillment_channel": "MFN",
        "marketplace_id": "A1F83G8C2ARO7P",
    }
    payload.update(overrides)
    return payload


def approval_for(payload, **overrides):
    approval = {
        "approved": True,
        "approval_type": runtime_gate.APPROVED_AMAZON_FBM_PUSH_TYPE,
        "approved_by": "stage5-test",
        "approval_id": "stage5-approval-1",
        "source": "bt38_command_center",
        "scope": {
            "sku": payload["sku"],
            "store_id": payload["store_id"],
            "listing_id": payload["listing_id"],
            "quantity": payload["quantity"],
        },
    }
    approval.update(overrides)
    return approval


def patch_valid_store_and_listing(monkeypatch, *, fulfillment="MFN", sku="FBM-STAGE5-01"):
    store = SimpleNamespace(
        id=101,
        platform="Amazon",
        is_active=True,
        fbm_sync_enabled=True,
        fulfillment_type="FBM",
    )
    listing = SimpleNamespace(
        id=202,
        store_id=101,
        external_sku=sku,
        amazon_fulfillment_channel=fulfillment,
    )
    monkeypatch.setattr(governed_execution, "_resolve_store", lambda store_id: store)
    monkeypatch.setattr(governed_execution, "_resolve_listing", lambda listing_id: listing)
    return store, listing


def open_stage5_gate(monkeypatch):
    monkeypatch.setattr(runtime_gate, "RUNTIME_GATE_FORCE_CLOSED", False)
    monkeypatch.setattr(runtime_gate, "GOVERNED_AMAZON_FBM_LIVE_ENABLED", True)


def test_stage5_defaults_remain_closed_before_any_live_validation(monkeypatch):
    assert runtime_gate.RUNTIME_GATE_FORCE_CLOSED is True
    assert runtime_gate.GOVERNED_AMAZON_FBM_LIVE_ENABLED is False
    payload = live_payload()
    monkeypatch.setattr(
        governed_execution,
        "_select_adapter",
        lambda _marketplace: (_ for _ in ()).throw(AssertionError("default-closed gate must not select adapter")),
    )

    result = governed_execution.submit_governed_marketplace_action(
        payload,
        actor="stage5-test",
        approval=approval_for(payload),
        dry_run=False,
    )

    assert result["governed"] is True
    assert result["execution_blocked"] is True
    assert result["runtime_gate_checked"] is True
    assert result["runtime_gate_allowed"] is False
    assert result["eligibility_checked"] is False


def test_stage5_internal_amazon_fbm_live_path_requires_exact_approval_and_valid_listing(monkeypatch):
    open_stage5_gate(monkeypatch)
    payload = live_payload()
    store, listing = patch_valid_store_and_listing(monkeypatch)
    adapter_calls = []

    class FakeAmazonFbmAdapter:
        def execute(self, action, adapter_payload):
            adapter_calls.append((action, adapter_payload))
            return {
                "success": True,
                "ok": True,
                "governed": True,
                "dry_run": False,
                "execution_blocked": False,
                "marketplace": "amazon",
                "adapter": "amazon_fbm",
                "action": action,
                "reason": "stage5 fake governed amazon fbm live adapter reached",
            }

    monkeypatch.setattr(governed_execution, "_select_adapter", lambda marketplace: FakeAmazonFbmAdapter())

    result = governed_execution.submit_governed_marketplace_action(
        payload,
        actor="stage5-test",
        approval=approval_for(payload),
        dry_run=False,
    )

    assert result["ok"] is True
    assert result["governed"] is True
    assert result["dry_run"] is False
    assert result["execution_blocked"] is False
    assert result["runtime_gate_allowed"] is True
    assert result["eligibility_checked"] is True
    assert result["adapter"] == "amazon_fbm"
    assert adapter_calls[0][0] == "push_inventory"
    assert adapter_calls[0][1]["_governed_store"] is store
    assert adapter_calls[0][1]["_governed_listing"] is listing
    assert adapter_calls[0][1]["_governed_dry_run"] is False


def test_stage5_live_path_requires_dry_run_false(monkeypatch):
    open_stage5_gate(monkeypatch)
    payload = live_payload()
    patch_valid_store_and_listing(monkeypatch)

    result = governed_execution.submit_governed_marketplace_action(
        payload,
        actor="stage5-test",
        approval=approval_for(payload),
        dry_run=True,
    )

    assert result["governed"] is True
    assert result["dry_run"] is True
    assert result["execution_blocked"] is True
    assert result["runtime_gate_allowed"] is False
    assert result["adapter"] == "amazon_fbm"


def test_stage5_approval_must_be_approved_and_typed_and_exactly_scoped(monkeypatch):
    open_stage5_gate(monkeypatch)
    payload = live_payload()
    patch_valid_store_and_listing(monkeypatch)
    approvals = [
        approval_for(payload, approved=False),
        approval_for(payload, approval_type="wrong_type"),
        approval_for(payload, scope={"sku": payload["sku"], "store_id": payload["store_id"], "listing_id": payload["listing_id"]}),
        approval_for(payload, scope={**approval_for(payload)["scope"], "quantity": payload["quantity"] + 1}),
        approval_for(payload, scope={**approval_for(payload)["scope"], "extra": "not allowed"}),
    ]
    monkeypatch.setattr(
        governed_execution,
        "_select_adapter",
        lambda _marketplace: (_ for _ in ()).throw(AssertionError("bad approval must not select adapter")),
    )

    for approval in approvals:
        result = governed_execution.submit_governed_marketplace_action(
            payload,
            actor="stage5-test",
            approval=approval,
            dry_run=False,
        )
        assert result["execution_blocked"] is True
        assert result["runtime_gate_allowed"] is False


def test_stage5_store_and_listing_validation_must_pass_before_adapter(monkeypatch):
    open_stage5_gate(monkeypatch)
    payload = live_payload()
    monkeypatch.setattr(governed_execution, "_resolve_store", lambda store_id: None)
    monkeypatch.setattr(
        governed_execution,
        "_select_adapter",
        lambda _marketplace: (_ for _ in ()).throw(AssertionError("invalid store/listing must not select adapter")),
    )

    result = governed_execution.submit_governed_marketplace_action(
        payload,
        actor="stage5-test",
        approval=approval_for(payload),
        dry_run=False,
    )

    assert result["execution_blocked"] is True
    assert result["runtime_gate_allowed"] is True
    assert result["eligibility_checked"] is True
    assert "missing store" in result["reason"].lower()


def test_stage5_fba_unknown_and_ebay_live_remain_blocked(monkeypatch):
    open_stage5_gate(monkeypatch)
    blocked_payloads = [
        live_payload(sku="FBA-STAGE5-01", amazon_fulfillment_channel="AFN"),
        live_payload(amazon_fulfillment_channel=""),
        {"marketplace": "ebay", "action": "push_inventory", "sku": "EB-STAGE5-01", "quantity": 1},
    ]
    monkeypatch.setattr(
        governed_execution,
        "_select_adapter",
        lambda _marketplace: (_ for _ in ()).throw(AssertionError("blocked marketplace must not select adapter")),
    )

    for payload in blocked_payloads:
        result = governed_execution.submit_governed_marketplace_action(
            payload,
            actor="stage5-test",
            approval=approval_for(live_payload()) if payload.get("marketplace") == "ebay" else approval_for(payload),
            dry_run=False,
        )
        assert result["execution_blocked"] is True
        assert result["governed"] is True


def test_stage5_http_route_remains_dry_run_only_and_old_paths_stay_shutdown():
    route_source = open("governed_routes.py", encoding="utf-8").read().split(
        '@governed_bp.post("/governed/actions/sku/dry-run")', 1
    )[1]
    assert "submit_governed_marketplace_action" in route_source
    assert "dry_run=True" in route_source
    assert "dry_run=False" not in route_source
    assert shutdown_http_guard.is_shutdown_path("/api/push-sku") is True
    assert shutdown_http_guard.is_shutdown_path("/api/sync/amazon/sku/FBA-STAGE5-01") is True
    assert getattr(queue_manager, "enqueue_sync_job")(1, queue_manager.JOB_PUSH_ITEM, {"sku": "FBM-STAGE5-01"})["execution_blocked"] is True
    assert sync_dispatcher.start_dispatcher()["execution_blocked"] is True
    assert sync_dispatcher.start_order_import_scheduler()["execution_blocked"] is True
