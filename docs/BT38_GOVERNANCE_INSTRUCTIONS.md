# BT38 Governance Instructions

## Non-negotiable restore and route-change rules

These instructions are locked for BT38_CLEAN_GOVERNED.

### 1. Restores must not reintroduce old routes

If the system is restored from an old backup, old route blocks, old workers, old sync paths, old push paths, or old UI action handlers must be treated as untrusted until audited.

A restore is not proof that the restored code is safe.

Every restore must be followed by a full route and execution-path audit before any branch, pull request, or deployment is created.

### 2. No fixing broken old routes

Do not repair old broken execution routes.

If a route is old, duplicated, circular, bypasses governed execution, bypasses warehouse truth, or has already caused instability, it must not be patched back into service.

Correct action:

1. Freeze or disable the old route safely.
2. Create a new clean governed route/path if functionality is still required.
3. Prove the new route is the only active path.
4. Only then remove or retire the old route.

### 3. No small patching of unstable route logic

Do not apply small line patches to unstable or legacy route blocks.

Allowed method:

- full block replacement only
- clean new route only when the old route is unsafe
- no regex/surgical patching unless explicitly approved for a tiny non-runtime label or display condition

### 4. Warehouse truth must stay governed

Warehouse truth must remain controlled by the governed architecture.

Legacy paths must not directly bypass governed execution for marketplace push/sync work.

Routes that mutate stock, push to marketplaces, import marketplace listing state, repair links, or rebuild mapping must be audited before being allowed to write live state.

### 5. Restore checklist before any branch or deployment

After any restore, backup rollback, branch reset, route replacement, or copied old file is introduced, run and review proof for:

- duplicate routes
- old route decorators
- old worker/scheduler startup paths
- old queue/job entrypoints
- direct marketplace push/sync routes
- WarehousePushCoordinator callers
- direct WarehouseStock.available_quantity mutation paths
- old product-linking repair/write endpoints
- old Amazon/eBay service live-call paths
- template buttons still pointing at frozen or old endpoints

No branch should be opened until this audit output is reviewed.

No deployment should happen until tests pass and the route audit proves only the approved governed path is active.

### 6. Proof required before branch and deploy

Every restored or newly-created route path must be proven by output before branch/PR/deploy:

- grep/audit output showing the old route is frozen or removed
- route map output showing only the expected active route
- compile check
- governed test suite
- production-safe deployment check

If proof is missing, stop.

### 7. Commercial rule

BT38 is a commercial system. Mistakes can affect live stock, marketplace listings, customer orders, and business trust.

When in doubt:

- audit first
- freeze unsafe old paths
- create a clean governed path
- prove it
- then deploy

Do not go around in circles repairing legacy routes.
