"""Compatibility binding for the existing MCFService API.

The UI and fee logic can keep using MCFService while live Amazon execution uses
the exact FBA store selected on each MCFOrder.
"""
from __future__ import annotations

from mcf_service import MCFService
from services.governed_mcf_execution import refresh_mcf_status, submit_mcf_order
import services.fbm_packlink_draft_alignment  # noqa: F401
import services.fbm_operational_autosave  # noqa: F401
import services.fbm_marketplace_order_update_alignment  # noqa: F401
import services.fbm_live_feed_alignment  # noqa: F401


def _submit(self, mcf_order):
    return submit_mcf_order(mcf_order)


def _refresh(self, mcf_order):
    return refresh_mcf_status(mcf_order)


MCFService.submit_mcf_to_amazon = _submit
MCFService.get_mcf_order_status = _refresh
