# Amazon Developer / Appstore Logo

This folder contains the canonical BT38 Inventory artwork used to keep the public website and Amazon Selling Partner Appstore branding aligned.

## Canonical GitHub brand source
The governed source of truth is:

`static/img/marketplaces/bt38-inventory-amazon-developer.svg`

Properties:
- Product identity: `BT38 Inventory`
- Shape: square
- SVG viewBox: `0 0 1000 1000`
- Dark background with white BT38 Inventory cube mark and product name

## Amazon upload variants
Amazon-required PNG variants must be rendered directly from the canonical GitHub SVG without changing the design:
- `300 x 300` PNG
- `220 x 220` PNG

The website may serve the canonical SVG while the Appstore receives PNG derivatives. The artwork, name, icon, layout and colours must remain the same.

## Important
- This is publication/compliance branding, not a Warehouse marketplace action icon.
- Do not replace it with the Amazon, eBay, Shopify or TikTok marketplace logos.
- Do not repurpose it as part of Warehouse marketplace-control UI work.
- Public landing, early-access, privacy, terms and support surfaces must use the `BT38 Inventory` identity.
- Amazon Appstore listing name must be `BT38 Inventory`.
- If the Appstore logo changes, update the canonical GitHub artwork first and render new Amazon upload variants from that same source.

## Pre-submit gate
Before submitting or resubmitting the Appstore listing:
1. Deploy the exact current PR #528 GitHub HEAD through the governed GitHub Actions workflow only.
2. Verify the live public site visibly uses `BT38 Inventory` and the canonical logo.
3. Verify `/privacy`, `/terms`, `/support` and `/apply` are publicly reachable.
4. Upload the 300 x 300 and 220 x 220 PNG derivatives generated from this canonical GitHub artwork.
5. Use only Appstore categories and functionality that are visibly described on the public website.

Purpose: keep Amazon Developer/Appstore branding separate from marketplace logos and prevent website/Appstore identity drift.
