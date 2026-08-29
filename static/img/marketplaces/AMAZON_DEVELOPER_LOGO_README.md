# Amazon Developer / Appstore Logo

This folder also contains the BT38 Inventory artwork used for the Amazon Selling Partner Appstore / Amazon Developer submission.

## Canonical Amazon submission asset
The exact PNG supplied for the Amazon submission is the master branding file and must be treated as the source of truth before deployment.

Expected master file properties:
- Product identity: `BT38 Inventory`
- Format: PNG
- Dimensions: `1254 x 1254`
- File size: `876615 bytes`
- SHA-256: `86c77624ded4798d72f0339d5ad6139456152fa6f4406c0a59fae840bedaf2a7`

## Important
- The BT38 Inventory Amazon Developer logo is publication/compliance branding, not a marketplace action icon.
- Do not replace it with the Amazon, eBay, Shopify or TikTok marketplace logos.
- Do not repurpose or modify it as part of Warehouse marketplace-control UI work.
- The public BT38 Inventory landing page, early-access pages and public compliance pages must use the same `BT38 Inventory` identity as the Amazon Appstore submission.
- Amazon-required logo variants must be derived from the same master artwork.
- The current SVG web representation is not the canonical master PNG.

## Pre-deploy gate
Do not treat Amazon Appstore branding alignment as complete until the exact canonical PNG above is committed to the branch and the public pages reference that PNG. Verify the committed PNG SHA-256 against the value above before deployment.

Purpose: prevent the Amazon Developer/Appstore branding asset from being confused with the marketplace logos used by the Warehouse page and prevent an approximate/recreated logo from being deployed as the Amazon-facing identity.
