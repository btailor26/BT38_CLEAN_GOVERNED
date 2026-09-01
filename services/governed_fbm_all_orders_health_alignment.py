"""Align FBM health to operational dispatch work while retaining history.

Every persisted FBM order remains classified, but the action count is driven only
by orders that still require dispatch plus unresolved carrier/mapping exceptions.
Dispatched orders remain visible as history and can be filtered independently.
No marketplace/provider calls or writes occur here.
"""
from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import joinedload

from extensions import db
from models import MarketplaceOrder
from governed_fbm_routes import _platform, _shipment_map
from services.fbm_shipping_state import shipment_confirmation_state
from services.governed_fulfillment_workspace_policy import fulfillment_section


def install_governed_fbm_all_orders_health_alignment(app) -> None:
    if getattr(app, "_bt38_fbm_all_orders_health_alignment_installed", False):
        return

    from services import governed_fbm_page_alignment as page_alignment

    def all_orders_health_summary() -> dict:
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
        order_rows = []
        for row in latest:
            key = (int(row.store_id), str(row.marketplace_order_id))
            profile = profiles.get(key) if _platform(row).strip().lower() == "amazon" else None
            if page_alignment._workspace_fbm_eligible(row, profile):
                order_rows.append((row, profile))

        shipments = _shipment_map([row for row, _ in order_rows])
        dispatch_due = dispatched = awaiting = overdue = mapping_review = 0
        returns = replacements = refund_issues = 0
        platform_counts = {}
        for row, profile in order_rows:
            platform = _platform(row).strip() or "Other"
            platform_counts[platform] = platform_counts.get(platform, 0) + 1
            section = fulfillment_section(
                fulfillment_type=getattr(row, "fulfillment_type", None),
                profile_channel=getattr(profile, "fulfillment_channel", None) if profile else None,
                status=getattr(row, "status", None),
                shipped_at=getattr(row, "shipped_at", None),
            )
            if section.family != "FBM":
                continue
            if section.view == "dispatch_due":
                dispatch_due += 1
            elif section.view == "dispatched":
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

        total = dispatch_due + dispatched
        risk_actions = overdue + mapping_review + returns + replacements + refund_issues
        health_base = max(1, total + returns + replacements + refund_issues)
        health_score = max(0, min(100, round(100 * (health_base - risk_actions) / health_base)))
        return {
            "period_mode": "operational",
            "period_label": "Current FBM work",
            "period_start": None,
            "period_end": None,
            "total": total,
            "ready": dispatch_due,
            "dispatch_due": dispatch_due,
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
            "shipping_actions": dispatch_due + overdue + mapping_review,
            "truncated": False,
        }

    def operational_controls(health: dict) -> str:
        return (
            '<div class="fbm-period-controls" aria-label="FBM work scope">'
            '<span class="badge bg-light text-dark border">Current FBM work</span>'
            '<span class="small text-muted">Dispatch due is active work; dispatched orders remain in history.</span>'
            '</div>'
        )

    page_alignment._health_summary = all_orders_health_summary
    page_alignment._period_controls = operational_controls
    app._bt38_fbm_all_orders_health_alignment_installed = True
    app.logger.info("BT38 FBM health aligned: dispatch due is active work; dispatched remains history; DB only")
