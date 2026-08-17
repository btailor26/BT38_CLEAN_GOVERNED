"""FBM shipment state owned by BT38.

These tables do not replace MarketplaceOrder. MarketplaceOrder remains the order
source of truth; these records only describe fulfilment execution for an order.
No marketplace, carrier, inventory, webhook, or MCF behaviour is triggered by
these models.
"""
from __future__ import annotations

from datetime import datetime

from extensions import db


class FBMOrderProfile(db.Model):
    """Shipping-specific marketplace facts persisted before FBM routing."""

    __tablename__ = "fbm_order_profiles"
    id = db.Column(db.Integer, primary_key=True)
    store_id = db.Column(db.Integer, db.ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    marketplace_order_id = db.Column(db.String(200), nullable=False, index=True)
    platform = db.Column(db.String(50), nullable=False, index=True)
    is_prime = db.Column(db.Boolean, nullable=True, index=True)
    is_premium = db.Column(db.Boolean, nullable=True)
    fulfillment_channel = db.Column(db.String(50), nullable=True)
    shipment_service_level = db.Column(db.String(100), nullable=True)
    latest_ship_at = db.Column(db.DateTime, nullable=True)
    source = db.Column(db.String(100), nullable=False, default="marketplace_shipping_profile")
    checked_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_error = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    __table_args__ = (db.UniqueConstraint("store_id", "marketplace_order_id", name="uq_fbm_order_profile_store_order"),)


class FBMRateQuote(db.Model):
    """Short-lived provider quote persisted so purchases cannot trust browser metadata."""

    __tablename__ = "fbm_rate_quotes"
    id = db.Column(db.Integer, primary_key=True)
    store_id = db.Column(db.Integer, db.ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    marketplace_order_id = db.Column(db.String(200), nullable=False, index=True)
    provider = db.Column(db.String(50), nullable=False, index=True)
    request_token = db.Column(db.Text, nullable=True)
    parcel = db.Column(db.JSON, nullable=False, default=dict)
    rates = db.Column(db.JSON, nullable=False, default=list)
    ineligible_rates = db.Column(db.JSON, nullable=False, default=list)
    expires_at = db.Column(db.DateTime, nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    used_at = db.Column(db.DateTime, nullable=True)

    @property
    def expired(self) -> bool:
        return bool(self.expires_at and datetime.utcnow() >= self.expires_at)


class FBMShipment(db.Model):
    """One physical FBM shipment for an existing marketplace order."""

    __tablename__ = "fbm_shipments"
    id = db.Column(db.Integer, primary_key=True)
    store_id = db.Column(db.Integer, db.ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    marketplace_order_id = db.Column(db.String(100), nullable=False, index=True)

    provider = db.Column(db.String(50), nullable=True, index=True)
    provider_shipment_id = db.Column(db.String(200), nullable=True, index=True)
    provider_carrier_id = db.Column(db.String(200), nullable=True)
    provider_service_id = db.Column(db.String(200), nullable=True)
    carrier = db.Column(db.String(100), nullable=True)
    service = db.Column(db.String(200), nullable=True)
    tracking_number = db.Column(db.String(200), nullable=True, index=True)

    label_url = db.Column(db.Text, nullable=True)
    label_format = db.Column(db.String(20), nullable=True)
    label_document_type = db.Column(db.String(30), nullable=True, default="LABEL")
    label_width = db.Column(db.Float, nullable=True)
    label_length = db.Column(db.Float, nullable=True)
    label_size_unit = db.Column(db.String(20), nullable=True)
    label_dpi = db.Column(db.Integer, nullable=True)
    label_page_layout = db.Column(db.String(50), nullable=True)
    label_source = db.Column(db.String(50), nullable=True)
    label_storage_ref = db.Column(db.Text, nullable=True)

    purchase_key = db.Column(db.String(200), nullable=True, unique=True, index=True)
    selected_rate_id = db.Column(db.String(300), nullable=True)
    purchase_status = db.Column(db.String(50), nullable=True, index=True)
    purchase_error = db.Column(db.Text, nullable=True)

    status = db.Column(db.String(50), nullable=False, default="awaiting_label", index=True)
    label_purchased_at = db.Column(db.DateTime, nullable=True)
    handover_due_at = db.Column(db.DateTime, nullable=True, index=True)
    carrier_accepted_at = db.Column(db.DateTime, nullable=True)
    first_movement_at = db.Column(db.DateTime, nullable=True)
    delivered_at = db.Column(db.DateTime, nullable=True)
    last_provider_status = db.Column(db.String(100), nullable=True)
    last_provider_checked_at = db.Column(db.DateTime, nullable=True)

    marketplace_confirmed_at = db.Column(db.DateTime, nullable=True)
    marketplace_confirmation_status = db.Column(db.String(50), nullable=True)
    marketplace_confirmation_error = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    __table_args__ = (db.Index("idx_fbm_shipment_order", "store_id", "marketplace_order_id"),)

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
