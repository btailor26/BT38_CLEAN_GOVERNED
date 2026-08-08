# Product Linking Release Gate

Product Linking relationship behaviour must NOT be approved from local pytest alone.

Local tests are structural/static checks only. They do not prove database/UI lifecycle behaviour because the local test environment is not connected to the real governed Neon relationship state.

## Mandatory release gate

Any change affecting Product Linking Link, Unlink, grouping, master selection, browser refresh, cache behaviour, or DB/UI relationship rendering must be tested against a disposable Neon branch copied from the current governed database state.

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

## Explicit unlink rule

Once a listing is linked, it remains linked indefinitely.

No timer, scheduler, focus event, refresh, browser cache operation, hydration, reconcile cycle, or automatic process may unlink it.

Only a fresh explicit user action on the exact listing followed by Confirm Unlink may issue the governed unlink request.

## Deployment rule

Do not deploy a Product Linking lifecycle change unless:

- candidate code is committed to an exact SHA;
- GitHub Deployment Readiness passes;
- GitHub Playwright passes;
- disposable Neon lifecycle test passes against that exact candidate;
- browser state and Neon state agree;
- production Neon has not been used as the test environment.

Local pytest results alone must never be described as proof that a Product Linking relationship change is production-ready.
