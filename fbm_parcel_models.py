"""Persisted FBM parcel-combination knowledge and shipment-order links.

MarketplaceOrder remains the sales/order source of truth and FBMShipment remains
the single physical-shipment authority. These tables only remember how a known
item combination is packed and which marketplace orders deliberately share one
existing physical shipment. They do not import orders, call marketplaces or
providers, buy labels, move stock, or create a second shipment system.
"""
from __future__ import annotations

from datetime import datetime

from extensions import db


class FBMParcelCombinationMapping(db.Model):
    """Verified packed parcel for an exact canonical SKU/quantity combination."""

    __tablename__ = "fbm_parcel_combination_mappings"

    id = db.Column(db.Integer, primary_key=True)
    combination_key = db.Column(db.String(64), nullable=False, unique=True, index=True)
    items = db.Column(db.JSON, nullable=False, default=list)
    total_units = db.Column(db.Integer, nullable=False, default=1)

    weight_kg = db.Column(db.Float, nullable=True)
    length_cm = db.Column(db.Float, nullable=True)
    width_cm = db.Column(db.Float, nullable=True)
    height_cm = db.Column(db.Float, nullable=True)

    verification_status = db.Column(db.String(40), nullable=False, default="verified", index=True)
    verified_at = db.Column(db.DateTime, nullable=True)
    verified_by = db.Column(db.String(120), nullable=True)
    source = db.Column(db.String(80), nullable=False, default="fbm_mapping_review")
    usage_count = db.Column(db.Integer, nullable=False, default=0)
    last_used_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def complete(self) -> bool:
        return all(
            value is not None and float(value) > 0
            for value in (self.weight_kg, self.length_cm, self.width_cm, self.height_cm)
        )


class FBMShipmentOrderLink(db.Model):
    """Attach multiple marketplace order identities to one existing FBMShipment.

    The shipment row stays the physical label/tracking authority. This link does
    not merge MarketplaceOrder records and does not create another shipment.
    """

    __tablename__ = "fbm_shipment_order_links"

    id = db.Column(db.Integer, primary_key=True)
    shipment_id = db.Column(
        db.Integer,
        db.ForeignKey("fbm_shipments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    store_id = db.Column(
        db.Integer,
        db.ForeignKey("stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    marketplace_order_id = db.Column(db.String(200), nullable=False, index=True)
    is_primary = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    shipment = db.relationship(
        "FBMShipment",
        backref=db.backref("order_links", lazy=True, cascade="all, delete-orphan"),
    )

    __table_args__ = (
        db.UniqueConstraint(
            "shipment_id",
            "store_id",
            "marketplace_order_id",
            name="uq_fbm_shipment_order_link_identity",
        ),
        db.Index(
            "idx_fbm_shipment_order_link_order",
            "store_id",
            "marketplace_order_id",
        ),
    )
