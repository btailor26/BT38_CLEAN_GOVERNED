"""Stage 5 contract tests for the one clean governed marketplace path."""

from __future__ import annotations

from types import SimpleNamespace

import governed_execution
import queue_manager
import shutdown_http_guard
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
        "approval_type": "amazon_fbm_single_sku_inventory_push",
        "approved_by": "pytest",
        "approval_id": "approval-stage5",
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


def open_stage5_gate(monkeypatch):
    monkeypatch.setattr(runtime_gate, "RUNTIME_GATE_FORCE_CLOSED", False)
    monkeypatch.setattr(runtime_gate, "GOVERNED_AMAZON_FBM_LIVE_ENABLED", True)


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


def test_stage5_defaults_remain_closed_before_any_live_validation(monkeypatch):
    payload = live_payload()
    patch_valid_store_and_listing(monkeypatch)

    result = governed_execution.submit_governed_marketplace_action(
        payload,
        actor="stage5-test",
        approval=approval_for(payload),
        dry_run=False,
    )

    assert result["execution_blocked"] is True
    assert result["runtime_gate_allowed"] is False
    assert "disabled" in result["reason"].lower()


def test_stage5_dual_flag_gate_requires_both_flags_before_adapter(monkeypatch):
    payload = live_payload()
    patch_valid_store_and_listing(monkeypatch)
    monkeypatch.setattr(
        governed_execution,
        "_select_adapter",
        lambda _marketplace: (_ for _ in ()).throw(AssertionError("partial gate must not select adapter")),
    )

    partial_states = [
        {"RUNTIME_GATE_FORCE_CLOSED": True, "GOVERNED_AMAZON_FBM_LIVE_ENABLED": False},
        {"RUNTIME_GATE_FORCE_CLOSED": True, "GOVERNED_AMAZON_FBM_LIVE_ENABLED": True},
        {"RUNTIME_GATE_FORCE_CLOSED": False, "GOVERNED_AMAZON_FBM_LIVE_ENABLED": False},
    ]

    for state in partial_states:
        monkeypatch.setattr(runtime_gate, "RUNTIME_GATE_FORCE_CLOSED", state["RUNTIME_GATE_FORCE_CLOSED"])
        monkeypatch.setattr(runtime_gate, "GOVERNED_AMAZON_FBM_LIVE_ENABLED", state["GOVERNED_AMAZON_FBM_LIVE_ENABLED"])
        result = governed_execution.submit_governed_marketplace_action(
            payload,
            actor="stage5-test",
            approval=approval_for(payload),
            dry_run=False,
        )
        assert result["execution_blocked"] is True
        assert result["runtime_gate_allowed"] is False
        assert result["eligibility_checked"] is False


def test_stage5_internal_amazon_fbm_live_path_requires_exact_approval_and_valid_listing(monkeypatch):
    open_stage5_gate(monkeypatch)
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
        actor="stage5-test",
        approval=approval_for(payload),
        dry_run=False,
    )

    assert result["ok"] is True
    assert result["runtime_gate_allowed"] is True
    assert result["eligibility_checked"] is True
    assert result["adapter"] == "amazon_fbm"
    assert calls and calls[0][0] == "push_inventory"
    assert calls[0][1]["_governed_dry_run"] is False
    assert calls[0][1]["_governed_store"].id == 101
    assert calls[0][1]["_governed_listing"].id == 202


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

    assert result["dry_run"] is True
    assert result["execution_blocked"] is True
    assert result["adapter"] == "amazon_fbm"
    assert "no live Listings API call" in result["reason"]


def test_stage5_approval_must_be_approved_and_typed_and_exactly_scoped(monkeypatch):
    open_stage5_gate(monkeypatch)
    payload = live_payload()
    patch_valid_store_and_listing(monkeypatch)
    approvals = [
        approval_for(payload, approved=False),
        approval_for(payload, approval_type="wrong_type"),
        approval_for(
            payload,
            scope={
                "sku": payload["sku"],
                "store_id": payload["store_id"],
                "listing_id": payload["listing_id"],
            },
        ),
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
        lambda _marketplace: (_ for _ in ()).throw(AssertionError("missing store must not select adapter")),
    )

    result = governed_execution.submit_governed_marketplace_action(
        payload,
        actor="stage5-test",
        approval=approval_for(payload),
        dry_run=False,
    )

    assert result["execution_blocked"] is True
    assert "missing store" in result["reason"]


def test_stage5_fba_unknown_and_ebay_live_remain_blocked(monkeypatch):
    open_stage5_gate(monkeypatch)

    fba_payload = live_payload(sku="FBA-STAGE5-01", amazon_fulfillment_channel="AFN")
    patch_valid_store_and_listing(monkeypatch, fulfillment="AFN", sku="FBA-STAGE5-01")

    result = governed_execution.submit_governed_marketplace_action(
        fba_payload,
        actor="stage5-test",
        approval=approval_for(fba_payload),
        dry_run=False,
    )
    assert result["execution_blocked"] is True
    assert "read-only" in result["reason"]

    unknown_payload = live_payload(sku="UNKNOWN-STAGE5-01", amazon_fulfillment_channel="")
    result = governed_execution.submit_governed_marketplace_action(
        unknown_payload,
        actor="stage5-test",
        approval=approval_for(unknown_payload),
        dry_run=False,
    )
    assert result["execution_blocked"] is True
    assert "unknown" in result["reason"].lower()

    ebay_payload = {
        "marketplace": "ebay",
        "action": "push_inventory",
        "sku": "EBAY-STAGE5-01",
        "store_id": 303,
        "listing_id": 404,
        "quantity": 3,
    }
    result = governed_execution.submit_governed_marketplace_action(
        ebay_payload,
        actor="stage5-test",
        approval=approval_for(ebay_payload),
        dry_run=False,
    )
    assert result["execution_blocked"] is True
    assert "disabled" in result["reason"].lower() or "ebay live" in result["reason"].lower()


def test_stage5_http_route_remains_dry_run_only_and_old_paths_stay_shutdown():
    route_source = open("governed_routes.py", encoding="utf-8").read().split(
        '@governed_bp.post("/governed/actions/sku/dry-run")',
        1,
    )[1]
    assert "submit_governed_marketplace_action" in route_source
    assert "dry_run=True" in route_source
    assert "AmazonAPIService" not in route_source
    assert "AmazonFbmAdapter" not in route_source
    assert shutdown_http_guard.is_shutdown_path("/api/push-sku") is True
    assert shutdown_http_guard.is_shutdown_path("/api/sync/amazon/sku/FBA-CG-UN-05") is True

    result = queue_manager.enqueue_sync_job(1, queue_manager.JOB_PUSH_ITEM, {"sku": "FBM-STAGE5-01"})
    assert result["execution_blocked"] is True
