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
10. All testing repairs for the active governed cycle must advance `fix/full-system-release-alignment` / PR #528. Main and side branches are not valid test-deployment sources while this contract is active.
11. The user is the architect/decision-maker. AI acts as cautious engineer.
12. No guesswork. Evidence first.
13. One clean wiring only. No circular restore/patch attempts.
14. Before page layout/UI changes, show visual proof/mockup first unless the user has explicitly approved the exact change.
15. Do not change the approved application shell logo, sidebar, top nav, nav colours, or warehouse layout unless explicitly approved. Public Amazon/Appstore branding work must remain isolated from the application shell and Warehouse controls.
16. Preserve mobile usability by default.
17. Use Git/version control discipline. Every deployable state must be an exact GitHub commit.
18. Do not deploy until compile/import/runtime and required contract checks pass and the user explicitly approves the exact current PR #528 HEAD.
19. Production deployment is manual only through `.github/workflows/deploy-fly.yml` using the current PR #528 HEAD SHA and Fly remote builder. Direct `fly deploy` from an operator PC is prohibited.
20. A successful test deployment does not authorize or perform a merge. PR #528 remains open and unmerged until separately approved.

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
11. Marketplace notification/webhook support is planned to reduce unnecessary polling.

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
