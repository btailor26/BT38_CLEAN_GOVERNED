"""Governed marketplace adapter base contracts.

Skeleton only: adapters must not import marketplace SDKs, create network clients,
or perform live marketplace calls. Every adapter returns a governed dry-run or
blocked contract until a future approved implementation is built.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping


ADAPTERS_DRY_RUN_ONLY = True
NO_MARKETPLACE_LIVE_CALLS = True


@dataclass(frozen=True)
class GovernedAdapterResult:
    """Standard adapter result returned by governed marketplace plugins."""

    success: bool
    ok: bool
    governed: bool
    dry_run: bool
    execution_blocked: bool
    marketplace: str
    adapter: str
    action: str
    reason: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable result dictionary."""
        return {
            "success": self.success,
            "ok": self.ok,
            "governed": self.governed,
            "dry_run": self.dry_run,
            "execution_blocked": self.execution_blocked,
            "marketplace": self.marketplace,
            "adapter": self.adapter,
            "action": self.action,
            "reason": self.reason,
            "payload": dict(self.payload),
        }


class GovernedMarketplaceAdapter:
    """Base skeleton adapter; subclasses only return dry-run/blocked results."""

    marketplace = "base"
    adapter_name = "base"

    def execute(self, action: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        """Return a blocked dry-run result; no live execution is permitted."""
        return self.blocked_result(
            action=action,
            payload=payload,
            reason="Marketplace adapters are skeleton dry-run only; no live calls are permitted.",
        )

    def blocked_result(self, action: str, payload: Mapping[str, Any], reason: str) -> Dict[str, Any]:
        """Build the standard blocked/dry-run result."""
        return GovernedAdapterResult(
            success=False,
            ok=False,
            governed=True,
            dry_run=True,
            execution_blocked=True,
            marketplace=self.marketplace,
            adapter=self.adapter_name,
            action=action,
            reason=reason,
            payload=payload,
        ).to_dict()
