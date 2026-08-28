"""Compatibility binding for the existing MCFService API.

The UI and fee logic can keep using MCFService while live Amazon execution uses
the exact FBA store selected on each MCFOrder.
"""
from __future__ import annotations

from app import app
from mcf_service import MCFService
from services.governed_mcf_execution import refresh_mcf_status, submit_mcf_order
import services.fbm_packlink_draft_alignment  # noqa: F401
from services.fbm_db_delivery_promise_alignment import (
    install_fbm_db_delivery_promise_alignment,
)


install_fbm_db_delivery_promise_alignment(app)


def _submit(self, mcf_order):
    return submit_mcf_order(mcf_order)


def _refresh(self, mcf_order):
    return refresh_mcf_status(mcf_order)


MCFService.submit_mcf_to_amazon = _submit
MCFService.get_mcf_order_status = _refresh
