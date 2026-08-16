# Amazon listing notification alignment audit

Status: GitHub-only alignment. No deploy, no merge.

## Preserved architecture

- Neon remains the canonical source of truth.
- `refresh_governed_listing_from_snapshot()` remains the sole listing writer.
- Existing Amazon SQS consumer remains the runtime intake target.
- Existing `ORDER_CHANGE` SQS wiring remains unchanged.
- Existing bounded Amazon listing recovery remains unchanged as fallback.
- No warehouse, Product Linking, FBA, order, or marketplace-push logic is changed by this alignment.

## Required Amazon ingress alignment

Amazon documents `LISTINGS_ITEM_STATUS_CHANGE` and `LISTINGS_ITEM_MFN_QUANTITY_CHANGE` under its Amazon EventBridge workflow. `LISTINGS_ITEM_STATUS_CHANGE` is emitted for listing create, delete and buyability transitions.

The intended path is therefore:

`SP-API listing notification -> SP-API EventBridge destination -> AWS partner event bus -> EventBridge rule -> existing BT38 Amazon SQS queue -> existing BT38 governed consumer -> exact Listings Items fetch -> canonical Neon writer`

The existing direct SQS destination remains correct for SQS-workflow notifications such as `ORDER_CHANGE`; it must not be repurposed for listing notifications.

## GitHub contract

The listing-subscription reconciler must:

1. Reuse an existing SP-API EventBridge destination.
2. Never select an SQS destination for listing notification types.
3. Never create or mutate Neon schema or canonical writers.
4. Fail explicitly with `amazon_existing_eventbridge_destination_missing` when the EventBridge destination has not been provisioned/associated yet.
5. Create/reuse `LISTINGS_ITEM_STATUS_CHANGE` and `LISTINGS_ITEM_MFN_QUANTITY_CHANGE` subscriptions using that EventBridge destination ID.
6. Leave infrastructure provisioning (partner bus association and EventBridge rule to the existing SQS queue) outside normal runtime execution.

This document records the alignment boundary only. It is not a deployment instruction.
