"""Tests for governed approval object construction."""

from __future__ import annotations

from services import governed_approval, runtime_gate


def test_command_center_approval_object_has_required_shape():
    approval = governed_approval.create_amazon_fbm_single_sku_approval(
        sku="FBM-STAGE5-01",
        store_id=101,
        listing_id=202,
        quantity=9,
        approved_by="pytest-user",
        approval_id="approval-fixed",
        created_at="2026-05-19T00:00:00+00:00",
    )

    assert approval == {
        "approval_id": "approval-fixed",
        "approved": True,
        "approval_type": runtime_gate.APPROVED_AMAZON_FBM_PUSH_TYPE,
        "approved_by": "pytest-user",
        "source": governed_approval.COMMAND_CENTER_SOURCE,
        "created_at": "2026-05-19T00:00:00+00:00",
        "scope": {
            "sku": "FBM-STAGE5-01",
            "store_id": 101,
            "listing_id": 202,
            "quantity": 9,
        },
    }
    assert governed_approval.approval_scope_is_exact(approval) is True


def test_command_center_approval_scope_must_be_exact():
    approval = governed_approval.create_amazon_fbm_single_sku_approval(
        sku="FBM-STAGE5-01",
        store_id=101,
        listing_id=202,
        quantity=9,
        approved_by="pytest-user",
    )

    missing = dict(approval)
    missing["scope"] = {
        "sku": "FBM-STAGE5-01",
        "store_id": 101,
        "listing_id": 202,
    }

    extra = dict(approval)
    extra["scope"] = dict(approval["scope"])
    extra["scope"]["extra"] = "not allowed"

    assert governed_approval.approval_scope_is_exact(missing) is False
    assert governed_approval.approval_scope_is_exact(extra) is False


def test_command_center_approval_matches_runtime_gate_contract(monkeypatch):
    payload = {
        "marketplace": "amazon",
        "action": "push_inventory",
        "sku": "FBM-STAGE5-01",
        "store_id": 101,
        "listing_id": 202,
        "quantity": 9,
        "amazon_fulfillment_channel": "MFN",
    }
    approval = governed_approval.create_amazon_fbm_single_sku_approval(
        sku=payload["sku"],
        store_id=payload["store_id"],
        listing_id=payload["listing_id"],
        quantity=payload["quantity"],
        approved_by="pytest-user",
    )

    class Command:
        marketplace = "amazon"
        action = "push_inventory"
        dry_run = False
        payload = payload
        approval = approval

    monkeypatch.setattr(runtime_gate, "RUNTIME_GATE_FORCE_CLOSED", False)
    monkeypatch.setattr(runtime_gate, "GOVERNED_AMAZON_FBM_LIVE_ENABLED", True)

    assert runtime_gate.is_runtime_allowed(Command()) is True


def test_command_center_approval_mismatch_fails_runtime_gate(monkeypatch):
    payload = {
        "marketplace": "amazon",
        "action": "push_inventory",
        "sku": "FBM-STAGE5-01",
        "store_id": 101,
        "listing_id": 202,
        "quantity": 9,
        "amazon_fulfillment_channel": "MFN",
    }
    approval = governed_approval.create_amazon_fbm_single_sku_approval(
        sku=payload["sku"],
        store_id=payload["store_id"],
        listing_id=payload["listing_id"],
        quantity=payload["quantity"] + 1,
        approved_by="pytest-user",
    )

    class Command:
        marketplace = "amazon"
        action = "push_inventory"
        dry_run = False
        payload = payload
        approval = approval

    monkeypatch.setattr(runtime_gate, "RUNTIME_GATE_FORCE_CLOSED", False)
    monkeypatch.setattr(runtime_gate, "GOVERNED_AMAZON_FBM_LIVE_ENABLED", True)

    assert runtime_gate.is_runtime_allowed(Command()) is False
