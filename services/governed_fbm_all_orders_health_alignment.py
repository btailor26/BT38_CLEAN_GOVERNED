"""Align FBM health cards to the full persisted FBM order set.

The FBM health/action strip is operational, not a historical report. It must not
hide an actionable order because that order was created outside a selected day or
month. This module reuses the existing DB authorities and replaces only the health
summary/read controls: all eligible persisted FBM order identities are considered,
with the latest MarketplaceOrder row per identity driving current state.

No marketplace/provider calls, writes, inventory mutation, or parallel order path.
"""
from __future__ import annotations

from html import escape

from sqlalchemy import func
from sqlalchemy.orm import joinedload

from extensions import db
from models import MarketplaceOrder
from governed_fbm_routes import _platform, _route_state, _shipment_map
from services.fbm_shipping_state import shipment_confirmation_state


def install_governed_fbm_all_orders_health_alignment(app) -> None:
    if getattr(app, "_bt38_fbm_all_orders_health_alignment_installed", False):
        return

    from services import governed_fbm_page_alignment as page_alignment

    def all_orders_health_summary() -> dict:
        """Return current FBM health across all persisted order identities."""
        eligible = (
            func.upper(func.coalesce(MarketplaceOrder.fulfillment_type, "")).notin_(("FBA", "AFN", "MCF")),
            ~func.lower(func.coalesce(MarketplaceOrder.status, "")).like("mcf_%"),
        )

        latest_ids = (
            db.session.query(func.max(MarketplaceOrder.id).label("id"))
            .filter(*eligible)
            .filter(MarketplaceOrder.store_id.isnot(None), MarketplaceOrder.marketplace_order_id.isnot(None))
            .group_by(MarketplaceOrder.store_id, MarketplaceOrder.marketplace_order_id)
            .subquery()
        )

        latest = (
            db.session.query(MarketplaceOrder)
            .join(latest_ids, MarketplaceOrder.id == latest_ids.c.id)
            .options(joinedload(MarketplaceOrder.store), joinedload(MarketplaceOrder.warehouse_stock))
            .order_by(MarketplaceOrder.id.desc())
            .all()
        )

        profiles = page_alignment._profile_map([
            row for row in latest if _platform(row).strip().lower() == "amazon"
        ])

        order_rows: list[MarketplaceOrder] = []
        for row in latest:
            key = (int(row.store_id), str(row.marketplace_order_id))
            profile = profiles.get(key) if _platform(row).strip().lower() == "amazon" else None
            if page_alignment._workspace_fbm_eligible(row, profile):
                order_rows.append(row)

        shipments = _shipment_map(order_rows)
        ready = dispatched = awaiting = overdue = mapping_review = 0
        returns = replacements = refund_issues = 0
        platform_counts: dict[str, int] = {}

        for row in order_rows:
            platform = _platform(row).strip() or "Other"
            platform_counts[platform] = platform_counts.get(platform, 0) + 1

            route_state = _route_state(row)
            if route_state == "Ready for FBM routing":
                ready += 1
            if route_state in {"Dispatched", "Tracking recorded"}:
                dispatched += 1

            shipment = shipments.get((int(row.store_id), str(row.marketplace_order_id)))
            if shipment:
                state = shipment_confirmation_state(shipment)
                if state == "awaiting_carrier_acceptance":
                    awaiting += 1
                elif state == "acceptance_overdue":
                    overdue += 1
                review = getattr(shipment, "mapping_review", None)
                if review is not None and getattr(review, "status", None) == "under_review":
                    mapping_review += 1

            status = str(getattr(row, "status", "") or "").strip().lower()
            if status in {"return_requested", "returned"}:
                returns += 1
            elif status in {"replacement_requested", "replacement"}:
                replacements += 1
            elif status in {"refund_requested", "refunded", "case_open", "dispute", "chargeback"}:
                refund_issues += 1

        total = len(order_rows)
        risk_actions = overdue + mapping_review + returns + replacements + refund_issues
        health_base = max(1, total + returns + replacements + refund_issues)
        health_score = max(0, min(100, round(100 * (health_base - risk_actions) / health_base)))
        shipping_actions = ready + overdue + mapping_review

        return {
            "period_mode": "all",
            "period_label": "All FBM orders",
            "period_start": None,
            "period_end": None,
            "total": total,
            "ready": ready,
            "dispatched": dispatched,
            "awaiting_acceptance": awaiting,
            "overdue": overdue,
            "mapping_review": mapping_review,
            "returns": returns,
            "replacements": replacements,
            "refund_issues": refund_issues,
            "platform_counts": dict(sorted(platform_counts.items(), key=lambda item: (-item[1], item[0].lower()))),
            "health_score": health_score,
            "risk_actions": risk_actions,
            "shipping_actions": shipping_actions,
            "truncated": False,
        }

    def all_orders_period_controls(health: dict) -> str:
        """Health is intentionally all-orders; date controls must not hide work."""
        return (
            '<div class="fbm-period-controls" aria-label="FBM health scope">'
            '<span class="badge bg-light text-dark border">All FBM orders</span>'
            '<span class="small text-muted">No date filter hides actionable orders.</span>'
            '</div>'
        )

    page_alignment._health_summary = all_orders_health_summary
    page_alignment._period_controls = all_orders_period_controls
    app._bt38_fbm_all_orders_health_alignment_installed = True
    app.logger.info(
        "BT38 FBM health aligned to all persisted FBM orders: latest DB state per order identity; no date-scoped operational blind spots"
    )
