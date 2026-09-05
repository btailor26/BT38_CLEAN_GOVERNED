"""Restore exact Amazon profile hydration to the bounded FBM workspace.

The bounded FBM page intentionally reads a small visible order window, but its
_profile_map replacement reduced Amazon classification to DB-only lookup. New
Amazon FBM orders could therefore render before FBMOrderProfile existed, losing
Prime/SFP lock, marketplace promise and the existing shipped-order readbacks.

This alignment keeps the bounded page and existing Amazon profile authority. On
the first profile-map read in an FBM request it hydrates only visible/selected
Amazon rows whose profile is missing or classification is incomplete. The
existing get_or_refresh_amazon_profile path owns Amazon reads and persistence;
no worker, poller, order importer, shipment table or marketplace write is added.
Subsequent profile-map reads in the same request (notably health aggregation)
remain DB-only so the health surface never fans out marketplace calls.
"""
from __future__ import annotations

from flask import g

from extensions import db
from services.fbm_amazon_order_profile import (
    AmazonOrderProfileError,
    get_or_refresh_amazon_profile,
)
import services.governed_fbm_page_alignment as _page_alignment


_original_profile_map = _page_alignment._profile_map


def _amazon_row(row) -> bool:
    store = getattr(row, "store", None)
    return str(getattr(store, "platform", "") or "").strip().lower() == "amazon"


def _profile_complete(profile) -> bool:
    if profile is None:
        return False
    # Prime classification is the routing lock. Fulfilment channel is the
    # seller-fulfilled authority used by the workspace eligibility guard.
    return (
        getattr(profile, "is_prime", None) is not None
        and bool(str(getattr(profile, "fulfillment_channel", "") or "").strip())
    )


def _governed_profile_map(rows):
    profiles = _original_profile_map(rows)

    # bounded_fbm_page calls _profile_map for its visible rows before health;
    # bounded_shipping_options calls it for the explicitly selected rows. Only
    # that first request-local call may hydrate Amazon. Later calls stay DB-only.
    if getattr(g, "_bt38_fbm_amazon_profile_hydration_checked", False):
        return profiles
    g._bt38_fbm_amazon_profile_hydration_checked = True

    refreshed = False
    for row in rows:
        try:
            if not _amazon_row(row) or row.store_id is None or not row.marketplace_order_id:
                continue
            key = (int(row.store_id), str(row.marketplace_order_id))
            if _profile_complete(profiles.get(key)):
                continue
            get_or_refresh_amazon_profile(row)
            refreshed = True
        except AmazonOrderProfileError:
            # Hydration is temporary compatibility only. Any failed unit must
            # be rolled back before the next visible row or page DB read.
            db.session.rollback()
            continue
        except Exception:
            # Marketplace/readback/autoflush failures must never poison the
            # shared request session or take down the FBM desk.
            db.session.rollback()
            continue

    return _original_profile_map(rows) if refreshed else profiles


if not getattr(_page_alignment, "_bt38_amazon_profile_hydration_restored", False):
    _page_alignment._profile_map = _governed_profile_map
    _page_alignment._bt38_amazon_profile_hydration_restored = True
