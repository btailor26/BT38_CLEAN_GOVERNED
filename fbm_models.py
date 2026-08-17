"""FBM shipment state owned by BT38.

These tables do not replace MarketplaceOrder. MarketplaceOrder remains the order
source of truth; these records only describe fulfilment execution for an order.
No marketplace, carrier, inventory, webhook, or MCF behaviour is triggered by
these models.
"""
from __future__ import annotations

from datetime import datetime

from extensions import db


class FBMShipment(db.Model):
    """One physical FBM shipment for an existing marketplace order."""

    __tablename__ = "fbm_shipments"

    id = db.Column(db.Integer, primary_key=True)

    # Existing BT38 order identity. Kept as values rather than a hard FK because
    # MarketplaceOrder can contain multiple line rows for one marketplace order.
    store_id = db.Column(db.Integer, db.ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    marketplace_order_id = db.Column(db.String(100), nullable=False, index=True)

    # Provider execution details. Payment stays with provider/marketplace account.
    provider = db.Column(db.String(50), nullable=True, index=True)  # amazon_buy_shipping, packlink, carrier_direct, manual
    provider_shipment_id = db.Column(db.String(200), nullable=True, index=True)
    carrier = db.Column(db.String(100), nullable=True)
    service = db.Column(db.String(200), nullable=True)
    tracking_number = db.Column(db.String(200), nullable=True, index=True)
    label_url = db.Column(db.Text, nullable=True)

    # Confirmation lifecycle.
    status = db.Column(db.String(50), nullable=False, default="awaiting_label", index=True)
    label_purchased_at = db.Column(db.DateTime, nullable=True)
    handover_due_at = db.Column(db.DateTime, nullable=True, index=True)
    carrier_accepted_at = db.Column(db.DateTime, nullable=True)
    first_movement_at = db.Column(db.DateTime, nullable=True)
    delivered_at = db.Column(db.DateTime, nullable=True)
    last_provider_status = db.Column(db.String(100), nullable=True)
    last_provider_checked_at = db.Column(db.DateTime, nullable=True)

    # Marketplace acknowledgement is separate from carrier acceptance.
    marketplace_confirmed_at = db.Column(db.DateTime, nullable=True)
    marketplace_confirmation_status = db.Column(db.String(50), nullable=True)
    marketplace_confirmation_error = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.Index("idx_fbm_shipment_order", "store_id", "marketplace_order_id"),
    )

    @property
    def carrier_acceptance_overdue(self) -> bool:
        if self.carrier_accepted_at or not self.handover_due_at:
            return False
        return datetime.utcnow() > self.handover_due_at


class FBMProviderCase(db.Model):
    """Carrier/provider support case attached to an FBM shipment."""

    __tablename__ = "fbm_provider_cases"

    id = db.Column(db.Integer, primary_key=True)
    shipment_id = db.Column(db.Integer, db.ForeignKey("fbm_shipments.id", ondelete="CASCADE"), nullable=False, index=True)

    provider = db.Column(db.String(50), nullable=False)
    case_type = db.Column(db.String(50), nullable=False, default="no_carrier_acceptance")
    provider_case_id = db.Column(db.String(200), nullable=True, index=True)
    status = db.Column(db.String(50), nullable=False, default="open")
    reason = db.Column(db.Text, nullable=True)
    opened_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    closed_at = db.Column(db.DateTime, nullable=True)
    last_response_at = db.Column(db.DateTime, nullable=True)
    last_error = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    shipment = db.relationship("FBMShipment", backref=db.backref("provider_cases", lazy=True, cascade="all, delete-orphan"))
