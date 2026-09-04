# BT38 MASTER RULES

## Core working rules
1. No UI changes unless explicitly approved.
2. Audit first.
3. Output must be shown first.
4. Output/results must be checked before moving forward.
5. Changes must be 100% aligned with the intended setup.
6. No deployment or forward movement until verified.
7. If any error appears in output, stop and audit that error first.
8. GitHub is the only source of production application files and deployment context.
9. Never clone, copy, overlay, build, test, or deploy BT38 application files from an operator PC. The operator PC may only inspect GitHub/Fly or dispatch the approved GitHub Actions workflow.
10. BT38 is a live commercial **production-only** runtime. Never deploy to, validate against, or use a test/staging Fly app, test database, test marketplace account, test deployment, or non-production clone as runtime authority. The commercial state/integrations are not guaranteed to exist there, so non-production failures are not valid evidence of production behaviour.
11. Never require a test/staging deployment before production. Runtime proof must come from the exact GitHub source plus the governed production configuration/data path. A production deployment still requires explicit user approval of the exact commit.
12. GitHub compile, syntax, static contract and source/deployment guard checks are allowed because they do not create or use a test runtime. They must not be described as a test deployment or used as a substitute for production runtime evidence.
13. All active governed work must advance `fix/full-system-release-alignment` / PR #528 unless the user explicitly changes the source branch. Main and side branches are not valid deployment sources while this contract is active.
14. The user is the architect/decision-maker. AI acts as cautious engineer.
15. No guesswork. Evidence first.
16. One clean wiring only. No circular restore/patch attempts.
17. Before page layout/UI changes, show visual proof/mockup first unless the user has explicitly approved the exact change.
18. Do not change the approved application shell logo, sidebar, top nav, nav colours, or warehouse layout unless explicitly approved. Public Amazon/Appstore branding work must remain isolated from the application shell and Warehouse controls.
19. Preserve mobile usability by default.
20. Use Git/version control discipline. Every deployable state must be an exact GitHub commit.
21. Do not deploy until compile/import/runtime and required governed source checks pass and the user explicitly approves the exact current PR #528 HEAD.
22. Production deployment is manual only through `.github/workflows/deploy-fly.yml` using the current PR #528 HEAD SHA and Fly remote builder. Direct `fly deploy` from an operator PC is prohibited.
23. Deployment never authorizes or performs a merge. PR #528 remains open and unmerged until separately approved.
24. BT38 UI/runtime improvement work is event-driven and session-driven by default. Zero polling is permitted for UI freshness, notifications, shipping handoff, marketplace handoff or page synchronization.
25. A committed event must update only the exact affected record/projection in the existing browser session. It must not reload/refetch the whole page, rebuild the table, rerun the initial page snapshot, or discard active tab/search/filter/selection/modal/scroll state.
26. Reuse the existing governed event/handoff transport. Do not add a second EventSource/SSE connection, parallel notification system, polling watcher, duplicate event bus or competing refresh controller.
27. No event means no UI-driven database work. Pages, bell and handoff paths must sleep when nothing changes: no heartbeat reads/writes, wake hydration, broad reconciliation or routine background rereads for presentation freshness.
28. The event source/committing workflow must carry the narrowest useful affected identity and already-known presentation fields. Do not make the receiving page broadly rediscover information that the committing workflow already knew.
29. Same-page and cross-page changes follow the same contract: canonical commit -> exact affected-record event/response -> exact session record update -> sleep.
30. The notification bell is informational only and must remain zero-query. It consumes already-published in-memory event data and never queries Neon, marketplace/provider APIs, orders, shipments, listings, Warehouse or logs to discover what happened.
31. Every future improvement touching UI freshness, events, handoff, shipping, dispatch, notifications or session state must include a contract test proving zero polling, zero broad rebuild/reread, exact affected-record handoff and session preservation. See `docs/EVENT_DRIVEN_SESSION_WORKFLOW.md`.

## BT38 inventory rules
1. Warehouse is the source of truth.
2. FBA/AFN stock is read-only.
3. FBM/MFN stock is editable/pushable.
4. MCF only applies to Amazon FBA stock.
5. MCF must not control normal warehouse/FBM stock.
6. Marketplace variations must be readable as their own SKU rows.
7. Grouping can connect variation SKUs to a master SKU, but individual SKU identity must remain visible.
8. Manual sync for bulk actions requires selected/ticked rows.
9. Single-row marketplace icon actions do not require checkbox selection.
10. Scheduled sync handles unselected/passive changes.
11. Marketplace notification/webhook support is event-driven and must not be replaced by polling.

## BT38 P&L rules
1. Uploaded financial files are temporary working data only.
2. Do not permanently store uploaded user financial data.
3. Check Files remains free.
4. Credit is only used when final report/output is downloaded.
5. New upload session replaces/clears previous temporary state.
6. Stock in hand is a core visibility metric.
7. VAT/GST/tax must be treated as important profit input.
8. If headers/tabs/transactions are unclear, stop and ask targeted mapping questions.
9. Saved mappings are user/source-specific and must not affect other users.

## Current priority
Fix broken marketplace connection state first, especially eBay connection failures, before further application-shell layout work. Explicitly approved public Amazon/Appstore compliance work may proceed only in the isolated public-site scope and must not alter Warehouse/runtime authority.
