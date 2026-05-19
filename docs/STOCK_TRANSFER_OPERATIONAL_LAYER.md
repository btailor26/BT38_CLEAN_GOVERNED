# BT38 Stock Transfer Operational Layer

## Problem

Current governed execution validates marketplace fulfillment state correctly.

However, there is currently no operational stock transfer layer that records:

- transfer intent
- transfer reason
- from channel
- to channel
- reconciliation state
- transfer audit history

The governed push layer is currently attempting to infer operational intent from listing state alone.

---

## Correct BT38 Architecture

Warehouse truth remains primary.

Operational flow should become:

```text
warehouse truth
→ stock transfer request
→ transfer reason
→ from_channel
→ to_channel
→ quantity
→ approval object
→ listing resolver refresh
→ governed push eligibility
→ marketplace adapter
→ marketplace response
→ audit trail
```

---

## Required Stock Transfer Object

Example:

```json
{
  "sku": "FBA-SR-AY-TC-80g",
  "quantity": 1,
  "reason": "moving stock from FBA to FBM",
  "from_channel": "AFN",
  "to_channel": "MFN",
  "requested_by": "user/system",
  "transfer_type": "stock_transfer",
  "approval_required": true
}
```

---

## Required Audit Checks

The audit layer must verify:

- listing resolver refreshed
- fulfillment channel changed
- linked listing still valid
- governed execution eligibility updated
- adapter reached only after operational transfer validation

---

## Important Rules

### Allowed

- governed operational transfer requests
- single SKU operational movement
- explicit transfer reasons
- audited channel movement
- warehouse truth validation
- governed push after validation

### Not Allowed

- direct payload override of fulfillment channel
- bypassing listing resolver
- direct FBA push
- automatic channel reassignment
- public live push routes
- queue/scheduler execution

---

## Current Proven State

Already proven in governed tests:

- runtime gate
- approval object
- governed execution path
- stop-transfer protection
- AFN/FBA read-only enforcement
- adapter blocking before live execution

Remaining operational gap:

```text
stock transfer operational state layer
```
