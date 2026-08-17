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
    """Shipping-specific marketplace facts persisted before FBM routing.

    MarketplaceOrder remains the commercial/order record. This profile stores
    marketplace shipping facts such as Amazon Prime/SFP eligibility so the FBM
    UI can make governed choices from the DB instead of guessing from titles,
    SKUs, or browser state.
    """

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

    # Audit where the marketplace fact came from; no full customer payload is
    # duplicated here.
    source = db.Column(db.String(100), nullable=False, default="marketplace_shipping_profile")
    checked_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_error = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("store_id", "marketplace_order_id", name="uq_fbm_order_profile_store_order"),
    )


class FBMShipment(db.Model):
    """One physical FBM shipment for an existing marketplace order."""

    __tablename__ = "fbm_shipments"

    id = db.Column(db.Integer, primary_key=True)

    # Existing BT38 order identity. Kept as values rather than a hard FK because
    # MarketplaceOrder can contain multiple line rows for one marketplace order.
    store_id = db.Column(db.Integer, db.ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    marketplace_order_id = db.Column(db.String(100), nullable=False, index=True)

    # Provider execution details. Payment stays with provider/marketplace account.
    provider = db.Column(db.String(50), nullable=True, index=True)  # amazon_buy_shipping, ebay_shipping, packlink, carrier_direct, manual
    provider_shipment_id = db.Column(db.String(200), nullable=True, index=True)
    carrier = db.Column(db.String(100), nullable=True)
    service = db.Column(db.String(200), nullable=True)
    tracking_number = db.Column(db.String(200), nullable=True, index=True)

    # Preserve the label exactly as supplied by the marketplace/provider. BT38
    # must not assume every provider returns the same file type or page size.
    label_url = db.Column(db.Text, nullable=True)
    label_format = db.Column(db.String(20), nullable=True)  # PDF, PNG, ZPL, provider-specific
    label_document_type = db.Column(db.String(30), nullable=True, default="LABEL")
    label_width = db.Column(db.Float, nullable=True)
    label_length = db.Column(db.Float, nullable=True)
    label_size_unit = db.Column(db.String(20), nullable=True)
    label_dpi = db.Column(db.Integer, nullable=True)
    label_page_layout = db.Column(db.String(50), nullable=True)
    label_source = db.Column(db.String(50), nullable=True)  # amazon, ebay, packlink, carrier_direct, manual
    label_storage_ref = db.Column(db.Text, nullable=True)  # internal/provider reference; never assumes a public URL

    # Purchase idempotency. A successful provider purchase must be persisted
    # before printing/marketplace confirmation, and the same key can never be
    # used to purchase postage twice.
    purchase_key = db.Column(db.String(200), nullable=True, unique=True, index=True)
    selected_rate_id = db.Column(db.String(300), nullable=True)
    purchase_status = db.Column(db.String(50), nullable=True, index=True)  # pending, purchased, failed, cancelled
    purchase_error = db.Column(db.Text, nullable=True)

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
