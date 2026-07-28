# Product Linking single-writer contract

Product Linking relationship writes are owned only by `governed_group_routes.py`.

Permanent identity:

- `WarehouseStock.master_product_group_id` is the product's immutable original group.
- `MarketplaceListing.warehouse_stock_id` is the permanent warehouse product identity.
- `MarketplaceListing.master_product_group_id` is the listing's current active group.

Allowed mutations:

- Initial assignment may set a missing warehouse original group once.
- Link changes only the listing's active group.
- Unlink restores the listing's active group from its warehouse original group.

Retired HTTP writers are blocked before route dispatch:

- `link-listing-to-warehouse`
- `unlink-listing`
- `product-linking-link`
- `/governed/groups/<group_id>/unlink-disabled`

Normal link and unlink operations must never clear warehouse identity, clear the original group, or run a full-dataset Product Linking refresh.
