"""Governed approval object helpers.

This module builds the approval dictionary consumed by runtime_gate.py.
It does not call adapters, routes, queues, schedulers, workers, or external APIs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4

from services import runtime_gate

COMMAND_CENTER_SOURCE = "bt38_command_center"
REQUIRED_SCOPE_KEYS = ("sku", "store_id", "listing_id", "quantity")


def create_amazon_fbm_single_sku_approval(
    *,
    sku: str,
    store_id: Any,
    listing_id: Any,
    quantity: Any,
    approved_by: str,
    approval_id: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Create the exact approval object required by runtime_gate.py."""
    return {
        "approval_id": approval_id or f"approval-{uuid4()}",
        "approved": True,
        "approval_type": runtime_gate.APPROVED_AMAZON_FBM_PUSH_TYPE,
        "approved_by": str(approved_by or "").strip(),
        "source": COMMAND_CENTER_SOURCE,
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "scope": {
            "sku": sku,
            "store_id": store_id,
            "listing_id": listing_id,
            "quantity": quantity,
        },
    }


def approval_scope_is_exact(approval: Mapping[str, Any]) -> bool:
    """Return whether approval scope contains exactly the required keys."""
    scope = dict(approval.get("scope") or {})
    return set(scope.keys()) == set(REQUIRED_SCOPE_KEYS)
