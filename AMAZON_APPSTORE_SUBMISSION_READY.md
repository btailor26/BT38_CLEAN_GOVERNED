# BT38 Inventory — Amazon Selling Partner Appstore Submission

Status: use this copy only after the current PR #528 head has passed GitHub readiness checks and has been deployed through the governed GitHub Actions workflow.

## Canonical identity
- App name: `BT38 Inventory`
- Website product name: `BT38 Inventory`
- Canonical website/Appstore artwork source: `static/img/marketplaces/bt38-inventory-amazon-developer.svg`
- Amazon image uploads: render the same canonical artwork as `300 x 300` PNG and `220 x 220` PNG without changing the design.

## Recommended listing categories
Select only the categories that are visibly supported by the public website:
1. `Inventory and Order Management`
2. `Ecommerce Solution Connectors`

Do not select future-channel categories or functionality that is not publicly launched and visibly explained.

## Short description
`Multi-channel inventory and order management for Amazon and eBay, with central stock control, fulfilment workflows and ecommerce marketplace connections.`

## App / product description
`BT38 Inventory is a multi-channel marketplace management system for Amazon and eBay sellers. It provides a central workspace for inventory and warehouse stock, marketplace orders, product linking, fulfilment and controlled cross-channel stock management.`

`Amazon functionality includes FBA inventory visibility, FBM order and dispatch workflows and Amazon Multi-Channel Fulfilment support. BT38 Inventory also connects supported eBay seller accounts so listing stock and marketplace orders can be managed alongside Amazon operations from the same system.`

## Ecommerce Solution Connectors explanation
`BT38 Inventory connects supported Amazon and eBay seller accounts to a central marketplace management workspace. Connected listings can be related to central warehouse stock, marketplace order activity can be viewed and managed, and supported cross-channel inventory and fulfilment workflows can be coordinated from BT38 Inventory.`

## Public URLs after governed deployment
- Product website: `https://bt38-prod.fly.dev/`
- Application / contact intake: `https://bt38-prod.fly.dev/apply`
- Support: `https://bt38-prod.fly.dev/support`
- Privacy: `https://bt38-prod.fly.dev/privacy`
- Terms: `https://bt38-prod.fly.dev/terms`

## Public website claims that must remain aligned
Current supported marketplace connections: Amazon and eBay.

Publicly described functionality:
- Central Inventory & Warehouse Management
- Marketplace Order Management
- Amazon FBA, FBM & MCF
- Multi-Channel Stock Control
- Shipping & Dispatch Management
- Team Access & Permissions
- Ecommerce Solution Connectors for Amazon and eBay

Do not describe TikTok, Shopify, Etsy, Facebook or other future integrations as current supported connections until they are actually launched and the public website is updated.

## Final pre-submit verification
1. PR #528 remains open and unmerged.
2. The exact current PR #528 head is the only deployment source.
3. Deployment Readiness and the Amazon public Appstore alignment contract are green.
4. Deploy only through the governed GitHub Actions -> Fly remote-builder workflow after explicit approval.
5. Open the live root page without signing in and confirm `BT38 Inventory` plus the canonical square logo are visible.
6. Open `/apply`, `/support`, `/privacy` and `/terms` without signing in and confirm the same `BT38 Inventory` identity is visible.
7. Upload the 300 x 300 and 220 x 220 PNG derivatives of the canonical GitHub logo to the Amazon listing.
8. App name in Amazon must be exactly `BT38 Inventory`.
9. Select only `Inventory and Order Management` and `Ecommerce Solution Connectors` unless another category is separately audited against live functionality.
10. Resubmit through Solution Provider Portal and reference the existing Amazon case if the portal requests it.

No merge is required for Appstore testing or submission. Production testing continues from the governed PR #528 lineage until separately approved for merge.
