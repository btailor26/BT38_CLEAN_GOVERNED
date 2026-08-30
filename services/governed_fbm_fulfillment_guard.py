"""Fail closed when Amazon fulfillment truth is not positively merchant fulfilled.

This is a guard on the existing FBM eligibility path only. It performs no reads,
writes, marketplace calls, inventory actions, or alternative order routing.
"""
from __future__ import annotations


def install_governed_fbm_fulfillment_guard() -> None:
    """Require persisted Amazon profile truth before an order can enter FBM."""
    from services import governed_fbm_page_alignment as page_alignment

    if getattr(page_alignment, "_bt38_amazon_fbm_fail_closed_installed", False):
        return

    original = page_alignment._workspace_fbm_eligible

    def amazon_fail_closed(row, profile=None) -> bool:
        platform = str(page_alignment._platform(row) or "").strip().lower()
        if platform != "amazon":
            return bool(original(row, profile))

        # Keep the shared persisted eligibility guard first. Amazon then needs
        # positive marketplace-owned FulfillmentChannel truth. Missing, stale or
        # ambiguous profile state is not sufficient to expose an order in FBM.
        if not page_alignment._is_fbm_eligible(row):
            return False

        profile_channel = (
            str(getattr(profile, "fulfillment_channel", "") or "")
            .strip()
            .upper()
            if profile is not None
            else ""
        )
        return profile_channel in {"MFN", "FBM"}

    page_alignment._workspace_fbm_eligible = amazon_fail_closed
    page_alignment._bt38_amazon_fbm_fail_closed_installed = True
