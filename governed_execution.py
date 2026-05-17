"""Single governed marketplace execution skeleton.

Stage 4: one governed Amazon FBM single-SKU live path may execute only when
approval, runtime gate, store/listing validation, and explicit MFN/FBM checks
all pass. No workers, schedulers, queue consumers, background loops, eBay live
calls, FBA calls, full-store sync, or direct route-to-marketplace calls exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional
from uuid import uuid4

from marketplace_adapters.amazon_fbm import AmazonFbmAdapter
from marketplace_adapters.ebay import EbayAdapter
from services import runtime_gate


AMAZON_FBM_LIVE_APPROVAL_TYPE = "amazon_fbm_single_sku_inventory_push"


GOVERNED_EXECUTION_SKELETON_ONLY = False
AMAZON_FBM_LIVE_PATH_ENABLED = True
ONE_GOVERNED_ENTRY_POINT = "submit_governed_marketplace_action"
ONE_GOVERNED_DISPATCHER = "dispatch_governed_action"
ONE_GOVERNED_EXECUTOR = "execute_governed_action"
NO_AUTO_WORKERS = True
NO_SCHEDULERS = True
NO_BACKGROUND_LOOPS = True
NO_UNGOVERNED_MARKETPLACE_LIVE_CALLS = True


@dataclass(frozen=True)
class GovernedCommand:
    """Command produced only by the approved governed entry point."""

    command_id: str
    marketplace: str
    action: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    actor: Optional[str] = None
    approval: Optional[Mapping[str, Any]] = None
    dry_run: bool = True


def submit_governed_marketplace_action(
    payload: Mapping[str, Any],
    *,
    actor: Optional[str] = None,
    approval: Optional[Mapping[str, Any]] = None,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """Only approved trigger for future marketplace execution.

    Runtime gate is checked before dispatch. Because the gate is currently
    force-closed, live execution remains blocked; dry-run commands may continue
    through dispatcher/executor to return governed eligibility and adapter proof
    contracts without making marketplace calls.
    """
    command = _build_command(payload, actor=actor, approval=approval, dry_run=dry_run)

    if not approval:
        return _blocked_result(
            command,
            reason="Approved trigger missing approval; governed execution blocked.",
            runtime_gate_checked=False,
            eligibility_checked=False,
        )

    gate_allowed = runtime_gate.is_runtime_allowed(command)
    if not gate_allowed and not dry_run:
        return _blocked_result(
            command,
            reason=runtime_gate.RUNTIME_GATE_MESSAGE,
            runtime_gate_checked=True,
            eligibility_checked=False,
            runtime_gate_allowed=False,
        )

    return dispatch_governed_action(command)


def dispatch_governed_action(command: GovernedCommand) -> Dict[str, Any]:
    """Single governed dispatcher; no workers, queues, or background starts."""
    return execute_governed_action(command)


def execute_governed_action(command: GovernedCommand) -> Dict[str, Any]:
    """Single governed executor; checks gate before adapter selection."""
    gate_allowed = runtime_gate.is_runtime_allowed(command)
    if not gate_allowed and not command.dry_run:
        return _blocked_result(
            command,
            reason=runtime_gate.RUNTIME_GATE_MESSAGE,
            runtime_gate_checked=True,
            eligibility_checked=False,
            runtime_gate_allowed=False,
        )

    eligibility = _check_marketplace_eligibility(command)
    if not eligibility["allowed"]:
        return _blocked_result(
            command,
            reason=eligibility["reason"],
            runtime_gate_checked=True,
            eligibility_checked=True,
            runtime_gate_allowed=gate_allowed,
        )

    adapter = _select_adapter(command.marketplace)
    adapter_payload = _build_adapter_payload(command, eligibility)
    adapter_result = adapter.execute(command.action, adapter_payload)
    adapter_result.update(
        {
            "command_id": command.command_id,
            "runtime_gate_checked": True,
            "runtime_gate_allowed": gate_allowed,
            "eligibility_checked": True,
            "dispatched_by": ONE_GOVERNED_DISPATCHER,
            "executed_by": ONE_GOVERNED_EXECUTOR,
        }
    )
    return adapter_result


def _build_command(
    payload: Mapping[str, Any],
    *,
    actor: Optional[str],
    approval: Optional[Mapping[str, Any]],
    dry_run: bool,
) -> GovernedCommand:
    marketplace = str(payload.get("marketplace") or "").strip().lower()
    action = str(payload.get("action") or "").strip().lower()
    return GovernedCommand(
        command_id=str(payload.get("command_id") or uuid4()),
        marketplace=marketplace,
        action=action,
        payload=dict(payload),
        actor=actor,
        approval=approval,
        dry_run=dry_run,
    )


def _select_adapter(marketplace: str):
    if marketplace == "amazon":
        return AmazonFbmAdapter()
    if marketplace == "ebay":
        return EbayAdapter()
    raise ValueError(f"Unsupported governed marketplace adapter: {marketplace or 'unknown'}")




def _build_adapter_payload(command: GovernedCommand, eligibility: Dict[str, Any]) -> Dict[str, Any]:
    adapter_payload = dict(command.payload)
    adapter_payload.update(
        {
            "_governed_dry_run": command.dry_run,
            "_governed_command_id": command.command_id,
            "_governed_approval_id": (command.approval or {}).get("approval_id"),
        }
    )
    if "store" in eligibility:
        adapter_payload["_governed_store"] = eligibility["store"]
    if "listing" in eligibility:
        adapter_payload["_governed_listing"] = eligibility["listing"]
    return adapter_payload

def _check_marketplace_eligibility(command: GovernedCommand) -> Dict[str, Any]:
    if command.marketplace == "amazon":
        fulfillment = str(
            command.payload.get("amazon_fulfillment_channel")
            or command.payload.get("fulfillment_channel")
            or command.payload.get("fulfillment")
            or ""
        ).strip().upper()
        sku = str(command.payload.get("sku") or "")
        if fulfillment not in {"AFN", "FBA", "MFN", "FBM"}:
            return {"allowed": False, "reason": "Amazon fulfillment is unknown; governed execution fails closed."}
        if fulfillment in {"AFN", "FBA"} or sku.upper().startswith("FBA-"):
            return {"allowed": False, "reason": "Amazon FBA/AFN is read-only; no FBA push path is permitted."}
        if command.dry_run:
            return {"allowed": True, "reason": "Amazon explicit FBM/MFN dry-run eligible."}
        return _check_amazon_fbm_live_eligibility(command, fulfillment, sku)

    if command.marketplace == "ebay":
        if command.dry_run:
            return {"allowed": True, "reason": "eBay dry-run eligible only after runtime gate approval."}
        return {"allowed": False, "reason": "eBay live execution is disabled; no eBay live calls are permitted."}

    return {"allowed": False, "reason": f"Unsupported marketplace: {command.marketplace or 'unknown'}"}


def _check_amazon_fbm_live_eligibility(command: GovernedCommand, fulfillment: str, sku: str) -> Dict[str, Any]:
    quantity_ok, quantity_or_reason = _coerce_quantity(command.payload.get("quantity"))
    if not quantity_ok:
        return {"allowed": False, "reason": quantity_or_reason}

    store_id = command.payload.get("store_id")
    listing_id = command.payload.get("listing_id")
    if store_id is None:
        return {"allowed": False, "reason": "Amazon FBM live push blocked: missing store_id."}
    if listing_id is None:
        return {"allowed": False, "reason": "Amazon FBM live push blocked: missing listing_id."}

    store = _resolve_store(store_id)
    if store is None:
        return {"allowed": False, "reason": "Amazon FBM live push blocked: missing store."}
    if "amazon" not in str(getattr(store, "platform", "")).lower():
        return {"allowed": False, "reason": "Amazon FBM live push blocked: store is not Amazon."}
    if getattr(store, "is_active", False) is not True:
        return {"allowed": False, "reason": "Amazon FBM live push blocked: store is not active."}
    if not _store_is_fbm_enabled(store):
        return {"allowed": False, "reason": "Amazon FBM live push blocked: store is not FBM-enabled."}

    listing = _resolve_listing(listing_id)
    if listing is None:
        return {"allowed": False, "reason": "Amazon FBM live push blocked: missing marketplace listing."}
    if _normalize_id(getattr(listing, "store_id", None)) != _normalize_id(store_id):
        return {"allowed": False, "reason": "Amazon FBM live push blocked: listing does not belong to store."}

    listing_sku = str(getattr(listing, "external_sku", "") or getattr(listing, "sku", "") or "").strip()
    if listing_sku != sku:
        return {"allowed": False, "reason": "Amazon FBM live push blocked: listing SKU does not match payload SKU."}

    listing_fulfillment = str(getattr(listing, "amazon_fulfillment_channel", "") or "").strip().upper()
    if listing_fulfillment in {"AFN", "FBA"}:
        return {"allowed": False, "reason": "Amazon FBM live push blocked: listing is FBA/AFN read-only."}
    if listing_fulfillment not in {"MFN", "FBM"}:
        return {"allowed": False, "reason": "Amazon FBM live push blocked: listing fulfillment is unknown."}
    if listing_fulfillment != fulfillment and {listing_fulfillment, fulfillment} != {"MFN", "FBM"}:
        return {"allowed": False, "reason": "Amazon FBM live push blocked: listing fulfillment does not match payload."}

    return {
        "allowed": True,
        "reason": "Amazon explicit FBM/MFN live eligible.",
        "store": store,
        "listing": listing,
        "quantity": quantity_or_reason,
    }


def _coerce_quantity(value):
    if value is None:
        return False, "Amazon FBM live push blocked: missing quantity."
    if isinstance(value, bool):
        return False, "Amazon FBM live push blocked: quantity must be an integer."
    try:
        quantity = int(value)
    except (TypeError, ValueError):
        return False, "Amazon FBM live push blocked: quantity must be an integer."
    if quantity < 0:
        return False, "Amazon FBM live push blocked: quantity must be >= 0."
    return True, quantity


def _resolve_store(store_id):
    from app import db
    from models import Store

    return db.session.get(Store, store_id)


def _resolve_listing(listing_id):
    from app import db
    from models import MarketplaceListing

    return db.session.get(MarketplaceListing, listing_id)


def _store_is_fbm_enabled(store) -> bool:
    if getattr(store, "fbm_sync_enabled", False) is True:
        return True
    if str(getattr(store, "fulfillment_type", "") or "").strip().upper() == "FBM":
        return True
    return str(getattr(store, "platform", "") or "").strip().lower() in {"amazonfbm", "amazon_fbm"}


def _normalize_id(value):
    return str(value).strip() if value is not None else None

def _blocked_result(
    command: GovernedCommand,
    *,
    reason: str,
    runtime_gate_checked: bool,
    eligibility_checked: bool,
    runtime_gate_allowed: bool = False,
) -> Dict[str, Any]:
    return {
        "success": False,
        "ok": False,
        "governed": True,
        "dry_run": command.dry_run,
        "execution_blocked": True,
        "runtime_gate_checked": runtime_gate_checked,
        "runtime_gate_allowed": runtime_gate_allowed,
        "eligibility_checked": eligibility_checked,
        "marketplace": command.marketplace,
        "action": command.action,
        "command_id": command.command_id,
        "dispatched_by": None,
        "executed_by": None,
        "reason": reason,
    }
