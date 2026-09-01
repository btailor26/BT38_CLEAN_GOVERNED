"""Persisted shipping-cost authority attached to existing BT38 dispatches."""
from __future__ import annotations

from datetime import datetime

from extensions import db


class ShippingSpendLedger(db.Model):
    """One financial record per physical dispatch/shipment.

    Shipment lifecycle remains owned by FBMShipment.  This table stores only the
    financial fact so cost corrections cannot alter carrier/tracking state.
    """

    __tablename__ = "shipping_spend_ledger"

    id = db.Column(db.Integer, primary_key=True)
    dispatch_key = db.Column(db.String(300), nullable=False, unique=True, index=True)
    shipment_id = db.Column(db.Integer, db.ForeignKey("fbm_shipments.id", ondelete="SET NULL"), nullable=True, index=True)
    store_id = db.Column(db.Integer, db.ForeignKey("stores.id", ondelete="CASCADE"), nullable=True, index=True)
    marketplace_order_id = db.Column(db.String(200), nullable=True, index=True)
    fulfillment_family = db.Column(db.String(20), nullable=False, index=True)
    provider = db.Column(db.String(80), nullable=True, index=True)
    amount = db.Column(db.Numeric(12, 4), nullable=False)
    currency = db.Column(db.String(3), nullable=False, default="GBP")
    source = db.Column(db.String(100), nullable=False)
    source_reference = db.Column(db.String(300), nullable=True)
    confirmed = db.Column(db.Boolean, nullable=False, default=True, index=True)
    recorded_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
