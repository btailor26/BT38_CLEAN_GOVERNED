"""Proof tests for the governed execution skeleton.

Stage 2 only: no route wiring, no workers, no schedulers, no queue consumers,
no background loops, and no live marketplace calls.
"""

from pathlib import Path

import governed_execution
from marketplace_adapters.amazon_fbm import AmazonFbmAdapter
from marketplace_adapters.ebay import EbayAdapter
from services import runtime_gate

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_runtime_gate_remains_force_closed_and_dry_run_stays_blocked():
    assert runtime_gate.RUNTIME_GATE_FORCE_CLOSED is True
    assert runtime_gate.is_runtime_allowed(object()) is False

    result = governed_execution.submit_governed_marketplace_action(
        {
            "marketplace": "amazon",
            "action": "push_inventory",
            "sku": "FBM-SAFE-01",
            "amazon_fulfillment_channel": "MFN",
        },
        actor="test",
        approval={"approved": True, "source": "test"},
        dry_run=True,
    )

    assert result["governed"] is True
    assert result["dry_run"] is True
    assert result["execution_blocked"] is True
    assert result["runtime_gate_checked"] is True
    assert result["runtime_gate_allowed"] is False
    assert result["eligibility_checked"] is True
    assert result["dispatched_by"] == governed_execution.ONE_GOVERNED_DISPATCHER
    assert result["executed_by"] == governed_execution.ONE_GOVERNED_EXECUTOR
    assert "no live Listings API call" in result["reason"]


def test_missing_approval_blocks_before_runtime_dispatch(monkeypatch):
    def fail_if_dispatched(_command):
        raise AssertionError("dispatcher must not run without approval")

    monkeypatch.setattr(governed_execution, "dispatch_governed_action", fail_if_dispatched)

    result = governed_execution.submit_governed_marketplace_action(
        {"marketplace": "ebay", "action": "push_inventory", "sku": "EB-OD-CR-100g-X3"},
        actor="test",
    )

    assert result["governed"] is True
    assert result["execution_blocked"] is True
    assert result["runtime_gate_checked"] is False
    assert "approval" in result["reason"].lower()


def test_dispatcher_enters_single_executor_when_called_directly(monkeypatch):
    calls = []

    def fake_executor(command):
        calls.append(command.command_id)
        return {"ok": False, "execution_blocked": True, "command_id": command.command_id}

    monkeypatch.setattr(governed_execution, "execute_governed_action", fake_executor)
    command = governed_execution.GovernedCommand(
        command_id="cmd-test",
        marketplace="ebay",
        action="push_inventory",
        payload={"sku": "EB-OD-CR-100g-X3"},
        approval={"approved": True},
    )

    result = governed_execution.dispatch_governed_action(command)

    assert calls == ["cmd-test"]
    assert result["command_id"] == "cmd-test"


def test_amazon_adapter_blocks_fba_unknown_and_fbm_dry_run_without_live_call():
    adapter = AmazonFbmAdapter()

    fba = adapter.execute(
        "push_inventory",
        {"sku": "FBA-CG-UN-05", "amazon_fulfillment_channel": "AFN"},
    )
    unknown = adapter.execute(
        "push_inventory",
        {"sku": "UNKNOWN-01", "amazon_fulfillment_channel": ""},
    )
    fbm = adapter.execute(
        "push_inventory",
        {"sku": "FBM-SAFE-01", "amazon_fulfillment_channel": "MFN"},
    )

    assert fba["execution_blocked"] is True
    assert "read-only" in fba["reason"]
    assert unknown["execution_blocked"] is True
    assert "unknown" in unknown["reason"].lower()
    assert fbm["dry_run"] is True
    assert fbm["execution_blocked"] is True
    assert "no live Listings API call" in fbm["reason"]


def test_ebay_adapter_returns_dry_run_blocked_contract_without_live_call():
    result = EbayAdapter().execute(
        "push_inventory",
        {"sku": "EB-OD-CR-100g-X3", "marketplace": "ebay"},
    )

    assert result["marketplace"] == "ebay"
    assert result["dry_run"] is True
    assert result["execution_blocked"] is True
    assert "no live eBay API call" in result["reason"]


def test_skeleton_has_no_network_or_old_service_imports():
    governed = read("governed_execution.py")
    base_source = read("marketplace_adapters/base.py")
    amazon_source = read("marketplace_adapters/amazon_fbm.py")
    ebay_source = read("marketplace_adapters/ebay.py")
    forbidden_everywhere = [
        "requests",
        "ebay_service",
        "amazon_auth",
        "amazon_rest_api",
        "sp_api",
        "threading",
        "BackgroundScheduler",
        "APScheduler",
        "enqueue_sync_job",
    ]

    for marker in forbidden_everywhere:
        assert marker not in governed, marker
        assert marker not in base_source, marker
        assert marker not in amazon_source, marker
        assert marker not in ebay_source, marker

    assert "amazon_service" not in governed
    assert "amazon_service" not in base_source
    assert "amazon_service" not in ebay_source
    assert "from amazon_service import AmazonAPIService" in amazon_source
