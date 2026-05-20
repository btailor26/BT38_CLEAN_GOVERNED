"""
BT38 GOVERNED AMAZON INVENTORY HYDRATION

Single inventory truth path:

Amazon API inventory
→ AmazonFBAInventory
→ MarketplaceListing.last_marketplace_qty
→ WarehouseStock.available_quantity
→ warehouse page

Rules:
- FBA/AFN is read-only
- FBM/MFN uses warehouse truth
- No legacy sync paths
- No duplicate inventory writes
"""

from app import db
from models import (
    AmazonFBAInventory,
    MarketplaceListing,
    WarehouseStock,
)


def hydrate_amazon_inventory():

    rows = (
        db.session.query(AmazonFBAInventory)
        .all()
    )

    stats = {
        "fba_rows": 0,
        "listing_updates": 0,
        "warehouse_updates": 0,
    }

    for inv in rows:

        stats["fba_rows"] += 1

        qty = inv.available_quantity or 0

        listing = (
            db.session.query(MarketplaceListing)
            .filter(
                MarketplaceListing.warehouse_stock_id == inv.warehouse_stock_id
            )
            .first()
        )

        if listing:

            listing.last_marketplace_qty = qty

            stats["listing_updates"] += 1

            if (
                listing.normalized_amazon_fulfillment_channel
                in ("AFN", "FBA")
            ):

                ws = db.session.get(
                    WarehouseStock,
                    listing.warehouse_stock_id
                )

                if ws:

                    ws.available_quantity = qty
                    ws.sellable_quantity = qty

                    stats["warehouse_updates"] += 1

    db.session.commit()

    return stats
