# BT38 MASTER RULES

## 1. Core Operating Rules

- GitHub is the only source of application code truth.
- Production is the only commercial runtime authority.
- Do not use operator-PC application files as source, test authority, build input, deployment input, or rollback source.
- Do not create or rely on a test/staging Fly app, test database, test marketplace account, or non-production clone as runtime authority.
- Audit first. Check and show evidence before moving to the next step.
- Do not change UI layout, protected navigation, colours, logos, or established workflow unless explicitly approved.
- Do not merge unless separately approved.
- Do not deploy unless the user explicitly approves the exact current PR head SHA.
- The user is the architect. AI acts as a cautious governed engineer and must not silently widen scope.
- Prefer one clean wiring over restore/patch/override chains.

## 2. Source / Branch Authority

- Active governed work is on PR #528 / `fix/full-system-release-alignment` unless explicitly changed by the user.
- Every audit, source check, contract check, build and deployment must identify the exact GitHub commit being evaluated.
- Do not assume the default branch represents the governed PR branch.
- Historical commits/backups may be used as evidence only. Never restore random backup code without proving it is the correct governed rollback point.

## 3. Runtime / Data Authority

- Warehouse is the source of truth for merchant-controlled stock.
- Product Linking defines relationships; it must not become a second stock authority.
- FBA inventory is read-only to BT38.
- FBM/eBay/other merchant-fulfilled inventory follows governed Warehouse authority.
- Marketplace-owned order, dispatch, tracking, delivery, return and refund facts remain marketplace-owned truth when persisted into their existing canonical BT38 record.
- Do not create duplicate records, tables, workers, pollers, event buses or write paths to represent truth already owned by an existing canonical record unless an explicitly approved architecture requires a genuinely separate business/physical entity.

## 4. Mandatory Event / Session Architecture

The governed UI freshness path is:

`governed action / marketplace event -> canonical DB commit -> exact affected-record event -> existing handoff transport -> existing browser session -> exact affected record update -> sleep`

Rules:

- Zero polling.
- No browser interval/timeout loop may repeatedly query Neon, marketplaces, providers, orders, shipments, listings, Warehouse or logs for freshness.
- No broad page reload/refetch/rebuild because one record changed.
- Reuse the existing event/session handoff. Do not introduce a second EventSource/SSE connection, notification system, polling watcher, duplicate queue or event bus.
- Events are signals, not a second database.
- Canonical DB/Warehouse records remain authority.
- An event must identify the exact affected record(s) needed by the presentation layer.
- If no event occurs, the UI freshness system sleeps and causes no UI-driven DB work.
- Hidden tabs/sessions may retain the exact committed event and apply it when visible; they must not compensate with broad refreshes.

## 5. Notification Bell

- The global notification bell is an informational presentation layer only.
- The bell must remain zero-query against Neon and zero-read against marketplaces/providers/carriers.
- Bell records are projected from the existing in-memory committed event stream/session path.
- The bell must not reconstruct notification history from orders, shipments, listings, Warehouse, provider data or logs.
- Do not add polling to keep the bell fresh.
- Do not create a second durable notification/event ledger merely for the bell.
- Browser-observed bounded history is presentation state only and may be lost across process/browser history boundaries; it is not canonical business truth.
- Opening the bell must not trigger marketplace/provider hydration or a Neon notification-history query.

## 6. Shipping / Dispatch Authority

- Marketplace dispatch/tracking truth belongs on the existing marketplace order identity unless a genuinely separate physical shipment has been purchased/recorded.
- A Packlink-purchased shipment is a real `FBMShipment` and Packlink/carrier owns its physical journey.
- An Amazon Buy Shipping purchased shipment is a real purchased shipment and its provider/carrier owns its physical journey.
- A manually purchased/recorded shipment may be represented by its real physical shipment row.
- Do not create a universal `FBMShipment(provider="marketplace")` merely to duplicate carrier/tracking/status already persisted on `MarketplaceOrder`.
- Marketplace confirmation may confirm dispatch/delivery but must not erase the physical provider authority of a genuinely purchased shipment.
- Do not invent carrier milestone timestamps.

## 7. Contract Tests

Changes touching any of the following require a focused contract/regression test:

- UI freshness or event/session handoff
- notification bell
- Warehouse/Product Linking authority
- shipping/dispatch/tracking
- marketplace read/write boundaries
- provider purchase/confirmation
- delivery promise persistence/read paths
- production image/database compatibility

Tests must prove the governed boundary, not merely implementation text.

## 8. Production Image / Database Alignment — HARD RELEASE GATE

Every production release must prove that the exact audited GitHub commit, the exact built Fly production image, and the production Neon database contract are mutually compatible.

Mandatory rules:

- Any change to SQLAlchemy models, tables, columns, constraints, indexes, raw SQL, persistence logic, canonical DB authority, or DB-backed service behaviour requires an explicit image/database compatibility audit before deployment.
- The exact production image must declare/derive the DB structures and persistence authorities it requires.
- The production Neon schema must be checked read-only against those requirements before rollout.
- Deployment must fail closed before production rollout if a required table, column, constraint, index or other structural dependency is absent or incompatible.
- Deployment must also fail closed when a change introduces a new persisted representation of business truth already owned by an existing canonical record unless that second persistence authority has been explicitly approved as a genuinely separate entity.
- A successful source/image fingerprint is not sufficient release proof. `GitHub SHA == Fly image` and `Fly image DB contract == Neon production schema/authority contract` must both pass.
- No automatic production schema mutation or migration is permitted merely to make a deployment pass. Any production migration requires its own governed audit and explicit approval.
- After rollout, run a bounded read-only production verification against critical DB-backed paths so schema/model incompatibility is detected immediately without marketplace/provider writes.
- Fly logs must not be the first place an image/database incompatibility is discovered; compatibility is a pre-deployment gate.

Required release chain:

`exact GitHub SHA -> exact production image -> read-only Neon compatibility proof -> deploy -> bounded read-only production verification`

## 9. One Truth / One Persistence Authority

- One business fact has one canonical persistence authority unless an explicitly approved design proves a separate entity is required.
- Before adding a table, row type, raw-SQL persistence path, compatibility store or duplicated projection, audit whether the fact already has a canonical owner.
- Presentation projections must remain projections. Do not persist them merely because a UI consumer expects a shipment/event/notification-shaped object.
- Compatibility code must not silently resurrect a retired table or persistence mechanism.
- If a new implementation requires compensating ranking/override layers to decide which duplicate record is authoritative, stop and audit the underlying ownership model before adding another alignment layer.

## 10. Deployment

Production deployment is manual GitHub Actions only and must use the exact approved PR head SHA.

Do not use direct `fly deploy` from the operator PC.

Approved operator pattern:

```bash
GH_EXE="$(find /c/Users/btail/AppData/Local/BT38-GitHubCLI -type f -iname gh.exe 2>/dev/null | head -1)"

"$GH_EXE" workflow run deploy-fly.yml \
  --repo btailor26/BT38_CLEAN_GOVERNED-Github \
  --ref fix/full-system-release-alignment \
  -f confirm_production_deploy=DEPLOY_GITHUB_COMMIT_TO_BT38_PROD \
  -f expected_commit=<EXACT_APPROVED_SHA>

"$GH_EXE" run watch \
  --repo btailor26/BT38_CLEAN_GOVERNED-Github \
  --exit-status
```

No merge is implied by deployment approval.
