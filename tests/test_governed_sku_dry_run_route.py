"""Proof tests for the single manual governed SKU dry-run route."""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import queue_manager
import shutdown_http_guard

ROOT = Path(__file__).resolve().parents[1]


class FakeBlueprint:
    def __init__(self, *_args, **_kwargs):
        pass

    def get(self, *_args, **_kwargs):
        return lambda func: func

    def post(self, *_args, **_kwargs):
        return lambda func: func


def import_routes_with_fake_flask(monkeypatch):
    request_obj = types.SimpleNamespace(headers={}, get_json=lambda silent=True: {})
    fake_flask = types.ModuleType("flask")
    fake_flask.Blueprint = FakeBlueprint
    fake_flask.jsonify = lambda payload: payload
    fake_flask.request = request_obj
    monkeypatch.setitem(sys.modules, "flask", fake_flask)
    sys.modules.pop("routes", None)
    return importlib.import_module("routes"), request_obj


def call_dry_run_route(monkeypatch, payload):
    routes, request_obj = import_routes_with_fake_flask(monkeypatch)
    request_obj.headers = {"X-Actor": "pytest"}
    request_obj.get_json = lambda silent=True: payload
    body, status = routes.governed_sku_dry_run()
    assert status == 200
    return body


def test_governed_dry_run_route_blocks_fba_sku(monkeypatch):
    result = call_dry_run_route(
        monkeypatch,
        {
            "marketplace": "amazon",
            "sku": "FBA-CG-UN-05",
            "amazon_fulfillment_channel": "AFN",
        },
    )

    assert result["governed"] is True
    assert result["dry_run"] is True
    assert result["execution_blocked"] is True
    assert result["runtime_gate_checked"] is True
    assert result["runtime_gate_allowed"] is False
    assert result["eligibility_checked"] is True
    assert "read-only" in result["reason"]


def test_governed_dry_run_route_blocks_unknown_amazon_fulfillment(monkeypatch):
    result = call_dry_run_route(
        monkeypatch,
        {
            "marketplace": "amazon",
            "sku": "AMZ-UNKNOWN-01",
            "amazon_fulfillment_channel": "",
        },
    )

    assert result["governed"] is True
    assert result["execution_blocked"] is True
    assert result["runtime_gate_allowed"] is False
    assert result["eligibility_checked"] is True
    assert "unknown" in result["reason"].lower()


def test_governed_dry_run_route_returns_fbm_dry_run_blocked(monkeypatch):
    result = call_dry_run_route(
        monkeypatch,
        {
            "marketplace": "amazon",
            "sku": "FBM-SAFE-01",
            "amazon_fulfillment_channel": "MFN",
        },
    )

    assert result["governed"] is True
    assert result["dry_run"] is True
    assert result["execution_blocked"] is True
    assert result["runtime_gate_allowed"] is False
    assert result["eligibility_checked"] is True
    assert result["adapter"] == "amazon_fbm"
    assert "no live Listings API call" in result["reason"]


def test_governed_dry_run_route_returns_ebay_dry_run_blocked(monkeypatch):
    result = call_dry_run_route(
        monkeypatch,
        {"marketplace": "ebay", "sku": "EB-OD-CR-100g-X3"},
    )

    assert result["governed"] is True
    assert result["dry_run"] is True
    assert result["execution_blocked"] is True
    assert result["runtime_gate_allowed"] is False
    assert result["eligibility_checked"] is True
    assert result["adapter"] == "ebay"
    assert "no live eBay API call" in result["reason"]


def test_route_calls_only_governed_entry_point_not_adapters_directly():
    source = (ROOT / "routes.py").read_text(encoding="utf-8")
    route_source = source.split('@bp.post("/governed/actions/sku/dry-run")', 1)[1]
    assert "submit_governed_marketplace_action" in route_source
    forbidden = ["marketplace_adapters", "AmazonFbmAdapter", "EbayAdapter", ".execute("]
    for marker in forbidden:
        assert marker not in route_source, marker


def test_old_paths_remain_blocked_after_new_dry_run_route():
    assert shutdown_http_guard.is_shutdown_path("/api/push-sku") is True
    assert shutdown_http_guard.is_shutdown_path("/api/sync/amazon/sku/FBA-CG-UN-05") is True
    result = queue_manager.enqueue_sync_job(1, queue_manager.JOB_PUSH_ITEM, {"sku": "FBM-SAFE-01"})
    assert result["execution_blocked"] is True
    assert result["old_sync_disabled"] is True
