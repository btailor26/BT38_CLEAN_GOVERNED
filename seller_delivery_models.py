"""Warehouse-owned configuration for Seller's Delivery Service.

The warehouse remains the physical origin authority. Marketplace stores do not
own or duplicate origin/radius configuration.
"""
from datetime import datetime

from extensions import db


class WarehouseSellerDeliveryConfig(db.Model):
    __tablename__ = "warehouse_seller_delivery_config"

    id = db.Column(db.Integer, primary_key=True)
    warehouse_id = db.Column(
        db.Integer,
        db.ForeignKey("warehouses.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    enabled = db.Column(db.Boolean, nullable=False, default=False)
    origin_postcode = db.Column(db.String(16), nullable=True)
    radius_miles = db.Column(db.Numeric(8, 2), nullable=True)
    service_name = db.Column(db.String(120), nullable=False, default="Seller's Delivery Service")
    cost_mode = db.Column(db.String(24), nullable=False, default="manual")
    flat_cost = db.Column(db.Numeric(12, 2), nullable=True)
    per_mile_cost = db.Column(db.Numeric(12, 2), nullable=True)
    currency = db.Column(db.String(3), nullable=False, default="GBP")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    warehouse = db.relationship("Warehouse", backref=db.backref("seller_delivery_config", uselist=False))
