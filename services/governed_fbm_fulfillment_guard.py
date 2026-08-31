"""Keep Amazon fulfillment truth scoped correctly inside the existing FBM path.

Normal Amazon MFN/FBM orders keep their existing Amazon Buy Shipping path. Only
positively identified Prime/SFP orders remain purchase/print gated until Amazon
production approval is enabled. No second shipping path or marketplace write is
introduced here.
"""
from __future__ import annotations

import os
from functools import wraps

from flask import jsonify


_FBA_FULFILLMENT = {"FBA", "AFN", "MCF"}
_AMAZON_BUY_SHIPPING_APPROVAL_ENV = "AMAZON_BUY_SHIPPING_APPROVED"


def _amazon_buy_shipping_approved() -> bool:
    value = str(os.getenv(_AMAZON_BUY_SHIPPING_APPROVAL_ENV) or "").strip().lower()
    return value in {"1", "true", "yes", "approved", "enabled"}


def _is_amazon_fba_row(row, profile) -> bool:
    platform = str(getattr(getattr(row, "store", None), "platform", "") or "").strip().lower()
    if platform != "amazon":
        return False

    persisted = str(getattr(row, "fulfillment_type", "") or "").strip().upper()
    profile_channel = str(getattr(profile, "fulfillment_channel", "") or "").strip().upper() if profile else ""
    return persisted in _FBA_FULFILLMENT or profile_channel in _FBA_FULFILLMENT


def _is_prime_sfp(profile) -> bool:
    return bool(profile is not None and getattr(profile, "is_prime", None) is True)


def install_governed_fbm_fulfillment_guard(app=None) -> None:
    """Keep FBA out of FBM and scope Amazon approval gating to Prime/SFP only."""
    import services.governed_fbm_lifecycle_alignment as lifecycle
    import services.governed_fbm_page_alignment as page

    if getattr(page, "_bt38_fbm_fulfillment_guard_installed", False):
        return

    # The lifecycle alignment previously used this approval switch as a blanket
    # Amazon gate. Neutralise only that blanket check; the Prime/SFP-only guard
    # below remains the final authority for rates and purchase actions.
    lifecycle._amazon_buy_shipping_approved = lambda: True

    original_latest = page._latest_distinct_fbm_rows
    original_eligible = page._workspace_fbm_eligible
    original_shipping_mode = page._workspace_shipping_mode
    original_provider_options = page._workspace_provider_options

    def guarded_eligible(row, profile=None):
        if _is_amazon_fba_row(row, profile):
            return False
        return original_eligible(row, profile)

    def guarded_latest(limit):
        rows, has_more = original_latest(limit)
        profiles = page._profile_map([row for row in rows if page._platform(row).strip().lower() == "amazon"])
        filtered = []
        for row in rows:
            profile = None
            if row.store_id is not None and row.marketplace_order_id:
                profile = profiles.get((int(row.store_id), str(row.marketplace_order_id)))
            if not _is_amazon_fba_row(row, profile):
                filtered.append(row)
        return filtered, has_more

    def guarded_shipping_mode(row, platform, profile):
        mode = dict(original_shipping_mode(row, platform, profile))
        if str(platform or "").strip().lower() == "amazon" and _is_prime_sfp(profile) and not _amazon_buy_shipping_approved():
            mode["marketplace_buy_shipping"] = False
            mode["recommended"] = "Amazon Buy Shipping pending approval"
            mode["reason"] = "Prime/SFP Buy Shipping purchase and printing remain locked until Amazon production approval is enabled."
        return mode

    def guarded_provider_options(row, profile):
        options = [dict(option) for option in original_provider_options(row, profile)]
        if page._platform(row).strip().lower() == "amazon" and _is_prime_sfp(profile) and not _amazon_buy_shipping_approved():
            for option in options:
                if str(option.get("provider") or "").strip().lower() == "amazon_buy_shipping":
                    option["available"] = False
                    option["recommended"] = False
                    option["message"] = "Prime/SFP Buy Shipping purchase and printing remain locked until Amazon production approval is enabled."
        return options

    page._workspace_fbm_eligible = guarded_eligible
    page._latest_distinct_fbm_rows = guarded_latest
    page._workspace_shipping_mode = guarded_shipping_mode
    page._workspace_provider_options = guarded_provider_options
    page._bt38_fbm_fulfillment_guard_installed = True

    if app is not None and not getattr(app, "_bt38_prime_buy_shipping_guard_installed", False):
        from governed_fbm_routes import _get_fbm_order, _platform, _profile_for

        def _wrap_prime_guard(endpoint: str) -> None:
            original = app.view_functions.get(endpoint)
            if original is None:
                return

            @wraps(original)
            def prime_guarded(order_id: int, *args, **kwargs):
                order = _get_fbm_order(order_id)
                if order is not None and _platform(order).strip().lower() == "amazon":
                    profile = _profile_for(order)
                    if _is_prime_sfp(profile) and not _amazon_buy_shipping_approved():
                        return jsonify({
                            "success": False,
                            "message": "Prime/SFP Amazon Buy Shipping purchase and printing are pending production approval. No rate or purchase action was attempted.",
                        }), 409
                return original(order_id, *args, **kwargs)

            app.view_functions[endpoint] = prime_guarded

        _wrap_prime_guard("governed_fbm.amazon_rates")
        _wrap_prime_guard("governed_fbm.amazon_purchase")
        app._bt38_prime_buy_shipping_guard_installed = True
