# BT38 MASTER RULES

## Core working rules
1. No UI changes unless explicitly approved.
2. Audit first.
3. Output must be shown first.
4. Output/results must be checked before moving forward.
5. Changes must be 100% aligned with the intended setup.
6. No deployment or forward movement until verified.
7. If any error appears in output, stop and audit that error first.
8. Bash-only workflows unless explicitly approved otherwise.
9. Every command block must start from the correct project directory:
   cd /c/Users/btail/_ARCHIVE_OLD_BT38/BT38 || exit 1
10. The user has zero coding experience and should not be expected to interpret code.
11. The user is the architect/decision-maker. AI acts as cautious engineer.
12. No guesswork. Evidence first.
13. One clean wiring only. No circular restore/patch attempts.
14. Before page layout/UI changes, show visual proof/mockup first.
15. Do not change logo, sidebar, top nav, nav colours, or approved shell unless explicitly approved.
16. Preserve mobile usability by default.
17. Use Git/version control discipline.
18. Do not deploy until compile/import/runtime checks pass and user approves.

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
Fix broken marketplace connection state first, especially eBay connection failures, before further layout work.
