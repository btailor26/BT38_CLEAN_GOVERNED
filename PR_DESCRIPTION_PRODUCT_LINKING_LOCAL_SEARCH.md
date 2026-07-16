## What changed

- Rebuilds the Product Linking browser row cache after the asynchronous product table is rendered.
- Keeps Product Linking search, filter changes, pagination, and Clear actions inside the browser session.
- Prevents the Clear link from reloading `/product-linking` and triggering another Fly → Neon data fetch.
- Adds contract tests for the async cache refresh and local Clear behaviour.

## Root cause

The shared page controller created its table cache before Product Linking finished its asynchronous `/governed/product-linking/data` request. The rendered rows were therefore absent from the browser cache, leaving local search disconnected from the actual data.

## Impact

After the initial Product Linking load, typing or submitting a search filters the already-rendered rows. It does not issue another database request. Explicit data-changing operations remain unchanged.

## Scope

Only `static/js/bt38-page-controller.js` and a focused contract test were added. No backend route, marketplace push, warehouse authority, sync, import, or runtime behaviour was changed.
