# Transfer Pending Review

## Purpose

Add a formal operational review state when a stock transfer requests FBM/MFN movement but governed validation still detects FBA/AFN or unresolved fulfillment state.

## Workflow

```text
StockTransfer
→ governed validation
→ fulfillment mismatch detected
→ Transfer Pending Review
→ operator review
→ resolver refresh
→ eligibility re-check
→ push allowed only after safe validation
```

## Why

Blocked transfers should not disappear operationally.

The system needs:
- visibility
- audit trail
- retry path
- controlled review

## Rules

Unsafe transfer state:

```text
≠ auto push
≠ silent rejection
```

Correct behavior:

```text
unsafe state
→ Transfer Pending Review
```
