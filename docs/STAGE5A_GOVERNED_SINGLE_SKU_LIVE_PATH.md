# BT38 Stage 5A — Governed Single-SKU Amazon FBM Live Path

## Current Safe Baseline

Baseline tag:

`STAGE4_GOVERNED_BASELINE_20260518`

Current production state:

- UI routes restored
- Governed dry-run route works
- Shutdown status route works
- `ENABLE_SYNC_WORKERS=false`
- `ENABLE_PUSH_JOBS=false`
- `ENABLE_SCHEDULERS=false`
- `IS_ACTIVE_PRIMARY=true`
- No background workers
- No schedulers
- No queue execution
- No live marketplace execution from public HTTP route

## Stage 5A Goal

Allow one controlled internal live path only:

`Amazon FBM/MFN single SKU inventory quantity update`

## Approved Flow

Command Center approval
→ approval object created
→ internal governed execution call
→ runtime gate
→ store validation
→ listing validation
→ fulfillment validation
→ exact approval scope match
→ Amazon FBM adapter
→ one governed Amazon inventory quantity call
→ audit log result
→ UI response

## Required Approval Scope

The approval scope must exactly match payload values:

- `sku`
- `store_id`
- `listing_id`
- `quantity`

No missing fields.
No extra fields.
No mismatched values.

## Allowed

- Amazon only
- FBM/MFN only
- Single SKU only
- Quantity update only
- Internal governed execution only
- Explicit approval required
- `dry_run=False` required for live path
- Full audit logging before and after execution

## Not Allowed

- Public HTTP live push route
- eBay live execution
- FBA/AFN execution
- Full inventory sync
- Batch push
- Queue execution
- Scheduler execution
- Worker execution
- Automatic retries
- Webhook-triggered push
- Background marketplace loops
- Direct page-to-marketplace calls

## Stage 5A Rule

Do not activate live execution until tests prove:

- default remains closed
- dry-run remains safe
- malformed approval cannot reach adapter
- FBA/AFN blocked before adapter
- eBay blocked before adapter
- unknown fulfillment blocked before adapter
- only exact approved Amazon FBM/MFN single-SKU command reaches adapter
