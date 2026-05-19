# BT38 Extend Existing Stock Transfer Layer

## Audit Result

The existing stock transfer system already contains most of the required operational architecture.

Existing proven functionality:

- transfer records
- transfer lifecycle
- transfer direction
- planned quantity
- received quantity
- sellable quantity
- damaged quantity
- reconciliation workflow
- receive confirmation
- transfer statuses
- audit timestamps
- FBA ↔ warehouse operational movement

The existing system should be extended instead of replaced.

---

## Current Problem

The governed execution system and stock transfer system are currently disconnected.

Current governed flow:

```text
payload
→ governed execution
→ listing resolver
→ fulfillment validation
→ adapter eligibility
```

Current transfer flow:

```text
transfer object
→ operational movement
→ reconciliation
→ warehouse state
```

These two systems must be connected.

---

## Correct BT38 Direction

Do not create another transfer system.

Extend the existing transfer operational layer.

Correct architecture:

```text
warehouse truth
→ stock transfer request
→ transfer reason
→ from_channel
→ to_channel
→ quantity
→ approval object
→ listing resolver refresh
→ governed eligibility
→ governed push
→ marketplace response
→ audit trail
```

---

## Missing Fields To Add

The following operational fields are still missing:

- transfer_reason
- from_channel
- to_channel
- approval_object_id
- governed_eligibility_state
- push_eligibility_state
- marketplace_sync_state
- resolver_refresh_state

---

## Important Operational Rule

The system must never rely on payload override as the source of truth.

Incorrect:

```text
payload override → push
```

Correct:

```text
operational transfer state
→ resolver validation
→ governed execution eligibility
```

---

## Proven Through Audit

Already proven:

- governed runtime gate
- governed approval object
- governed execution path
- AFN/FBA stop-transfer protection
- adapter blocking before execution
- transfer operational UI already exists

Remaining gap:

```text
wire governed execution into existing stock transfer operational layer
```
