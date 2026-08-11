# Product Linking Release Gate

Product Linking relationship behaviour must NOT be approved from local pytest alone.

Local tests are structural/static checks only. They do not prove database/UI lifecycle behaviour because the local test environment is not connected to the real governed Neon relationship state.

## Permanent authority invariant

Product Linking manages relationships only. Its Push and push-settings controls are shortcuts into the Warehouse-controlled governed inventory path.

The invariant is:

`Product Linking shortcut -> group_id + warehouse_stock_id -> Warehouse saved sellable quantity -> shared governed group service -> FBA/AFN skip or FBM/eBay governed write -> committed affected-ID UI event -> targeted Product Linking/Warehouse refresh -> sleep`

Product Linking must never:

- calculate or own marketplace quantity;
- create its own marketplace push engine;
- write FBA/AFN inventory;
- use a webhook-specific competing push path;
- refresh the whole DB or full page to hide an affected-record mismatch;
- poll the DB/marketplaces to discover ordinary webhook changes.

Warehouse remains the sole FBM/eBay quantity authority. Webhooks update canonical FBA or Warehouse/order truth and then use the same Warehouse-controlled correction path. A correct final number reached through a second path is still a release-gate FAIL.

## Mandatory release gate

Any change affecting Product Linking Link, Unlink, grouping, master selection, browser refresh, cache behaviour, push shortcut, webhook handoff, or DB/UI relationship rendering must be tested against a disposable Neon branch copied from the current governed database state.

The candidate application code must be connected to that disposable Neon branch.

The complete browser-to-database journey must be proven before production deployment.

Required lifecycle:

1. Confirm initial Neon relationship state.
2. Link the marketplace listing through the candidate UI.
3. Confirm Neon committed the expected current group.
4. Confirm the browser renders the same relationship.
5. Refresh/refocus/wait and prove no automatic unlink occurs.
6. Attempt master unlink and prove it is blocked.
7. Click a removable member's Unlink control.
8. Prove clicking Unlink alone does not mutate Neon.
9. Close or cancel the confirmation and prove Neon remains unchanged.
10. Press Confirm Unlink.
11. Confirm exactly one governed unlink event occurred.
12. Confirm Neon returned the listing to its permanent/original relationship.
13. Confirm the browser renders that same original position.
14. Relink and confirm the complete cycle again where required.
15. Trigger Product Linking Push and prove it sends relationship identity only; Warehouse supplies the saved quantity.
16. Confirm every writable current-group member receives the same Warehouse-controlled quantity.
17. Confirm FBA/AFN members are reported read-only/skipped and no FBA marketplace write occurs.
18. Trigger the equivalent Warehouse group push and prove it uses the same shared governed service and produces the same DB result.
19. Trigger an Amazon/eBay webhook affecting a grouped listing and prove canonical DB truth updates first, followed by the same Warehouse-controlled correction path.
20. Confirm the UI event carries all exact affected listing, Warehouse and group IDs and refreshes only those records.
21. Confirm no-change/duplicate webhook events do not wake or refresh the Product Linking page.
22. Confirm rapid consecutive webhook changes are all preserved; no earlier event is lost or overwritten.
23. In the deployed test environment, measure webhook receipt -> committed DB truth -> visible affected record and prove it completes within 2 seconds for the required path.
24. Confirm no whole-database scan, routine full-page refresh, heartbeat or idle DB/browser polling was introduced.

## Explicit unlink rule

Once a listing is linked, it remains linked indefinitely.

No timer, scheduler, focus event, refresh, browser cache operation, hydration, reconcile cycle, or automatic process may unlink it.

Only a fresh explicit user action on the exact listing followed by Confirm Unlink may issue the governed unlink request.

## Deployment rule

Do not approve a Product Linking lifecycle change unless:

- candidate code is committed to an exact SHA;
- the whole integrated branch remains in draft/unmerged state during candidate testing;
- GitHub Deployment Readiness passes;
- GitHub Playwright passes;
- disposable Neon lifecycle test passes against that exact candidate;
- Product Linking shortcut, Warehouse push and webhook correction converge on one Warehouse-controlled path;
- browser state and Neon state agree for all affected records;
- changed records meet the deployed 2-second UI freshness requirement;
- no-change events leave pages asleep;
- production Neon has not been used as the test environment.

Local pytest results alone must never be described as proof that a Product Linking relationship change is production-ready.
