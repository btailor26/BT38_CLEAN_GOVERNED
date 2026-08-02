"""BT38 governed marketplace execution.

Single authority model:
UI -> governed route -> SystemConfig fuse box -> governed execution -> adapter

This module is the final live-execution choke point.
It still validates marketplace payload safety, but live execution now also
fails closed unless services.runtime_action_guard allows the action from the
SystemConfig + Store fuse box.

No workers.
No schedulers.
No queue consumers.
No background loops.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict
from uuid import uuid4


AMAZON_FBM_LIVE_APPROVAL_TYPE = "amazon_fbm_single_sku_inventory_push"
ONE_GOVERNED_ENTRY_POINT = "submit_governed_marketplace_action"


@dataclass
class GovernedCommand:
    command_id: str
    marketplace: str
    action: str
    payload: Dict[str, Any]
    dry_run: bool
    actor: str = "system"


def submit_governed_marketplace_action(
    *,
    payload: Dict[str, Any],
    dry_run: bool = True,
    actor: str = "system",
    actor_user = None,
    approval_type: str | None = None,
    approval_id: str | None = None,
) -> Dict[str, Any]:
    """Single governed marketplace execution entrypoint.

    This function:
    - normalizes command payload
    - performs marketplace safety eligibility checks
    - asks the SystemConfig fuse box before any live execution
    - runs the adapter only after the fuse box allows it
    - returns a safe result shape
    """

    command = _build_command(payload=payload, dry_run=dry_run, actor=actor)

    eligibility = _check_marketplace_eligibility(command)
    if not eligibility["allowed"]:
        return _blocked(
            command,
            reason=eligibility["reason"],
            eligibility=eligibility,
        )

    if command.dry_run:
        return {
            "ok": True,
            "success": True,
            "governed": True,
            "dry_run": True,
            "marketplace": command.marketplace,
            "action": command.action,
            "command_id": command.command_id,
            "reason": eligibility.get("reason", "Governed dry-run eligible."),
            "payload": _safe_payload(command.payload),
            "fuse_box_checked": False,
            "execution_started": False,
        }

    fuse_guard = _check_fuse_box_authority(
        command,
        eligibility,
        actor_user=actor_user,
    )
    if not fuse_guard.get("allowed"):
        return _blocked(
            command,
            reason=fuse_guard.get("reason", "Fuse box blocked governed execution."),
            eligibility=eligibility,
            fuse_box_checked=True,
            guard=fuse_guard,
            execution_started=False,
        )

    payload_for_adapter = dict(command.payload)
    payload_for_adapter["_governed_command_id"] = command.command_id
    payload_for_adapter["_governed_approval_type"] = approval_type
    payload_for_adapter["_governed_approval_id"] = approval_id
    payload_for_adapter["_governed_dry_run"] = False
    payload_for_adapter["_governed_fuse_box_checked"] = True

    if eligibility.get("store") is not None:
        payload_for_adapter["_governed_store"] = eligibility["store"]
    if eligibility.get("listing") is not None:
        payload_for_adapter["_governed_listing"] = eligibility["listing"]
    if eligibility.get("quantity") is not None:
        payload_for_adapter["quantity"] = eligibility["quantity"]

    adapter = _adapter_for(command.marketplace)
    if adapter is None:
        return _blocked(command, reason=f"No governed adapter for marketplace: {command.marketplace}")

    result = adapter.execute(command.action, payload_for_adapter)

    return {
        "ok": bool(result.get("ok") or result.get("success")),
        "success": bool(result.get("success") or result.get("ok")),
        "governed": True,
        "dry_run": False,
        "marketplace": command.marketplace,
        "action": command.action,
        "command_id": command.command_id,
        "reason": result.get("reason") or result.get("message") or "Governed adapter completed.",
        "adapter_result": _safe_payload(result),
        "fuse_box_checked": True,
        "guard": _safe_payload(fuse_guard),
    }


def _build_command(*, payload: Dict[str, Any], dry_run: bool, actor: str) -> GovernedCommand:
    payload = dict(payload or {})
    marketplace = str(payload.get("marketplace") or "").strip().lower()
    action = str(payload.get("action") or "").strip().lower()

    return GovernedCommand(
        command_id=str(payload.get("command_id") or uuid4()),
        marketplace=marketplace,
        action=action,
        payload=payload,
        dry_run=bool(dry_run),
        actor=actor or "system",
    )


def _adapter_for(marketplace: str):
    if marketplace == "amazon":
        from marketplace_adapters.amazon_fbm import AmazonFbmAdapter

        return AmazonFbmAdapter()

    if marketplace == "ebay":
        from marketplace_adapters.ebay import EbayAdapter

        return EbayAdapter()

    return None


def _check_fuse_box_authority(
    command: GovernedCommand,
    eligibility: Dict[str, Any],
    actor_user=None,
) -> Dict[str, Any]:
    """Ask the single SystemConfig fuse box before any live adapter execution.

    Manual UI pushes continue to require the manual push fuse and user access.
    Existing webhook and auto-push sources are classified as automatic so they
    use the same runtime push fuses without pretending to be manual actions.
    """
    from services.runtime_action_guard import is_runtime_action_allowed

    action_type = _runtime_action_type(command.action)
    store = eligibility.get("store") or _resolve_store(command.payload.get("store_id"))
    source = str(command.payload.get("source") or "").strip().lower()
    automatic_push = bool(
        action_type == "push"
        and (
            source.startswith("webhook_")
            or "auto_push" in source
            or "automatic_push" in source
        )
    )

    return is_runtime_action_allowed(
        store=store,
        action_type=action_type,
        manual=not automatic_push,
        context={
            "source": source or "governed_execution",
            "command_id": command.command_id,
            "marketplace": command.marketplace,
            "action": command.action,
            "listing_id": command.payload.get("listing_id"),
            "store_id": command.payload.get("store_id"),
            "actor": command.actor,
            "actor_user": actor_user,
            "automatic_push": automatic_push,
            "authority": "SystemConfig fuse box",
        },
    )


def _runtime_action_type(action: str) -> str:
    action = str(action or "").strip().lower()
    if "push" in action:
        return "push"
    if "sync" in action:
        return "sync"
    if "import" in action:
        return "import"
    return action


def _check_marketplace_eligibility(command: GovernedCommand) -> Dict[str, Any]:
    if command.marketplace == "amazon":
        fulfillment = str(
            command.payload.get("amazon_fulfillment_channel")
            or command.payload.get("fulfillment_channel")
            or command.payload.get("fulfillment")
            or ""
        ).strip().upper()

        sku = str(command.payload.get("sku") or "")

        if not fulfillment:
            return {"allowed": False, "reason": "Amazon fulfillment is unknown; governed execution fails closed."}
        if fulfillment in {"AFN", "FBA"}:
            return {"allowed": False, "reason": "Amazon FBA/AFN is read-only; no FBA push path is permitted."}
        if command.dry_run:
            return {"allowed": True, "reason": "Amazon explicit FBM/MFN dry-run eligible."}
        return _check_amazon_fbm_live_eligibility(command, fulfillment, sku)

    if command.marketplace == "ebay":
        if command.dry_run:
            return {"allowed": True, "reason": "eBay dry-run eligible."}
        return _check_ebay_live_eligibility(command)

    return {"allowed": False, "reason": f"Unsupported marketplace: {command.marketplace or 'unknown'}"}


def _check_ebay_live_eligibility(command: GovernedCommand) -> Dict[str, Any]:
    store_id = command.payload.get("store_id")
    listing_id = command.payload.get("listing_id")

    if store_id is None:
        return {"allowed": False, "reason": "eBay live push blocked: missing store_id."}
    if listing_id is None:
        return {"allowed": False, "reason": "eBay live push blocked: missing listing_id."}

    store = _resolve_store(store_id)
    if store is None:
        return {"allowed": False, "reason": "eBay live push blocked: missing store."}
    if "ebay" not in str(getattr(store, "platform", "")).lower():
        return {"allowed": False, "reason": "eBay live push blocked: store is not eBay."}
    if getattr(store, "is_active", False) is not True:
        return {"allowed": False, "reason": "eBay live push blocked: store is not active."}

    store_mode = str(getattr(store, "store_mode", "") or "").strip().lower()
    if store_mode != "live":
        return {"allowed": False, "reason": f"eBay live push blocked: store_mode={store_mode or 'unknown'}."}

    listing = _resolve_listing(listing_id)
    if listing is None:
        return {"allowed": False, "reason": "eBay live push blocked: missing marketplace listing."}
    if _normalize_id(getattr(listing, "store_id", None)) != _normalize_id(store_id):
        return {"allowed": False, "reason": "eBay live push blocked: listing does not belong to store."}

    quantity_ok, quantity_or_reason = _coerce_quantity(command.payload.get("quantity"))
    if not quantity_ok:
        return {"allowed": False, "reason": f"eBay live push blocked: {quantity_or_reason}"}

    return {
        "allowed": True,
        "reason": "eBay governed live execution eligible.",
        "store": store,
        "listing": listing,
        "quantity": quantity_or_reason,
    }


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
    if getattr(store, "fbm_sync_enabled", False) is not True:
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
    if not listing_fulfillment:
        return {"allowed": False, "reason": "Amazon FBM live push blocked: listing fulfillment is unknown."}
    if listing_fulfillment != fulfillment:
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
        return False, "missing quantity."
    try:
        quantity = int(value)
    except (TypeError, ValueError):
        return False, "quantity must be an integer."
    if quantity < 0:
        return False, "quantity must be >= 0."
    return True, quantity


def _resolve_store(store_id):
    from app import db
    from models import Store

    try:
        return db.session.get(Store, int(store_id))
    except Exception:
        return None


def _resolve_listing(listing_id):
    from app import db
    from models import MarketplaceListing

    try:
        return db.session.get(MarketplaceListing, int(listing_id))
    except Exception:
        return None


def _normalize_id(value):
    try:
        return int(value)
    except Exception:
        return value


def _safe_payload(value: Any):
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if key in {"store", "listing", "_governed_store", "_governed_listing"}:
                if item is None:
                    cleaned[key] = None
                else:
                    cleaned[key] = {
                        "id": getattr(item, "id", None),
                        "name": getattr(item, "name", None),
                        "platform": getattr(item, "platform", None),
                        "store_id": getattr(item, "store_id", None),
                        "sku": getattr(item, "external_sku", None) or getattr(item, "sku", None),
                    }
            elif key in {"access_token", "refresh_token", "api_key", "client_secret", "cert_id"}:
                cleaned[key] = "***"
            elif isinstance(item, (str, int, float, bool)) or item is None:
                cleaned[key] = item
            else:
                cleaned[key] = str(item)
        return cleaned
    return value


def _blocked(command: GovernedCommand, *, reason: str, **extra) -> Dict[str, Any]:
    payload = {
        "success": False,
        "ok": False,
        "governed": True,
        "dry_run": command.dry_run,
        "marketplace": command.marketplace,
        "action": command.action,
        "command_id": command.command_id,
        "reason": reason,
        "payload": _safe_payload(command.payload),
    }
    payload.update(extra)
    return payload