# BT38 Event-Driven Session Workflow

## Status

This contract is mandatory for all BT38 alignment, improvement, UI, marketplace, shipping, handoff and runtime work.

It extends the PSS governed workflow and the one-clear-path contract. If a proposed change conflicts with this document, the change must stop and be redesigned before implementation.

## Core rule

BT38 is event-driven and session-driven.

**Zero polling. Zero routine rebuilds. Zero broad rereads after an event.**

A page session is loaded once from canonical committed truth. After that, committed events update only the exact affected record or exact affected UI projection inside that existing browser session.

The browser session must not rebuild the page, refetch the whole page, reconstruct the whole table, rescan the database, or recreate an already-loaded workflow merely because one record changed.

## Mandatory event path

`governed action / marketplace event -> canonical DB commit -> exact affected-record event -> existing handoff transport -> existing browser session -> update exact affected record -> sleep`

The handoff is a notification of committed change, not a second authority.

Database/Warehouse truth remains authoritative. The event carries enough already-known identity and presentation state for the browser to update the affected projection without discovering the change through a broad database read.

## Zero-polling contract

No BT38 page, bell, shipping journey, handoff, notification surface, Product Linking surface, Warehouse surface, FBM surface, MCF surface, Listings surface or FBA surface may use polling as normal operation.

Prohibited normal-operation patterns include:

- `setInterval` or recursive short-delay request loops;
- long polling;
- heartbeat SQL reads or writes;
- periodic page refreshes;
- periodic marketplace/provider reads;
- periodic notification reads;
- browser wake/visibility DB hydration merely to check whether anything changed;
- background full-table reconciliation used as UI freshness authority.

A bounded scheduled business reconciliation may exist only where the governed runtime explicitly requires it for marketplace correctness. It must never be used to drive page freshness or replace exact event handoff.

## No-rebuild contract

After a committed event, the system must not:

- reload the page;
- fetch the current page HTML to reconstruct state;
- rebuild the whole table;
- rerender every row;
- rerun the initial page query merely to find the changed record;
- refetch a 300-row or other bounded snapshot because one record changed;
- recreate a modal/workspace/session state that is already open;
- discard browser-local search, page, tab, selection, scroll, filter or workflow state.

A full page read is allowed when the user explicitly navigates, reloads, changes a server-backed page boundary, or requests data that is not in the current session. It is not allowed as the response to a normal committed event.

## Exact-record update contract

Every event-driven improvement must answer these questions before coding:

1. What canonical action commits the truth?
2. What exact record identity changed?
3. What already-known fields are required by the visible projection?
4. Which existing event/handoff path carries them?
5. Which exact DOM/session record will be updated?
6. How does the session remain intact afterward?
7. What proves no polling, broad reread or rebuild was introduced?

Events must carry the narrowest useful identity, including where applicable:

- `store_id`;
- marketplace/platform;
- `order_id`;
- marketplace order item identity;
- `seller_sku` / SKU;
- `listing_id`;
- `warehouse_stock_id`;
- `group_id`;
- `shipment_id`;
- lifecycle/event type;
- exact changed presentation fields already known by the committing workflow.

Do not make the receiving page query broadly for information the committing workflow already had.

## Session-driven UI contract

The current browser session owns presentation state such as:

- active tab/work area;
- search text;
- filters;
- pagination/session window;
- selected records;
- expanded rows;
- open modal/workspace;
- scroll position;
- unsaved presentation-only choices.

A committed event must preserve that session state and modify only the affected record/projection.

If the affected record is not loaded in the current session, record the session as having newer committed truth where needed, but do not rebuild the current session. The record becomes visible through normal user navigation/search/page loading or an explicitly designed exact-record fetch.

## Page-to-page and same-page changes

The same rule applies whether the event originates:

- on another page;
- on the same page;
- from a marketplace webhook;
- from a governed user action;
- from label purchase/dispatch;
- from carrier handoff/tracking;
- from Product Linking;
- from Warehouse/group propagation;
- from MCF/FBA lifecycle processing.

Same-page actions must update the affected row directly from the successful governed response or exact committed event. They must not trigger a whole-page refresh afterward.

Cross-page events use the existing event handoff and update only the affected record if that record exists in the open session.

## Handoff contract

The existing handoff/event system must be reused. No improvement may create a parallel event bus, second SSE connection, second notification system, polling watcher or competing browser refresh controller.

Handoff characteristics:

- publish only after successful canonical commit;
- carry exact affected identity/state;
- preserve consecutive events;
- signal-only transport performs zero SQL;
- one browser-profile transport may fan out to tabs using the existing browser channel/leader mechanism;
- receiving surfaces apply the event to the current session only;
- no event means sleep and zero UI-driven database activity.

## Bell contract

The bell is an informational event projection only.

It consumes already-published in-memory event data. It never queries the database, marketplace, provider, shipment table, order table, listing table, Warehouse table, SystemLog or SyncLog to discover what happened.

The bell is not an event ledger and is not business authority. Process restart may clear its short in-memory history. Canonical pages/database remain the source of truth.

## Shipping / dispatch handoff contract

Shipping follows the same event rules:

`label/dispatch action -> governed persistence commit -> exact shipment/order event -> current FBM session exact-row update`

Label purchase must update only that order/shipment row. Marketplace dispatch confirmation must update only that order/shipment row. Carrier acceptance/movement/delivery must update only that order/shipment row.

Do not reload `/fbm`, refetch the FBM HTML snapshot, rebuild all journey rows, or query all shipments because one shipment changed.

## Improvement workflow

For every future improvement, PSS must include an event-impact audit before implementation.

### Problem

Prove the current event source, commit point, affected identity, session owner and any unnecessary polling/reread/rebuild.

### Solution

Prefer extending the existing event payload or exact-record mutation path. Do not add a broad refresh because it is easier.

### Solve

Implement the smallest exact-record update and add a contract test proving:

- no polling;
- no full-page reload/refetch;
- no broad snapshot reread caused by the event;
- no second event transport;
- exact affected identity is carried;
- current browser session state is preserved;
- only the affected record/projection changes.

## Mandatory release gates

Any change touching UI freshness, marketplace events, shipping, dispatch, notifications, handoff or session behaviour fails release readiness if it introduces any of the following:

- polling;
- heartbeat DB activity for UI freshness;
- event-triggered full-page fetch/reload;
- event-triggered broad table/snapshot rebuild;
- second SSE/EventSource or parallel handoff system;
- bell DB query;
- DOM/session state used as business authority;
- broad DB read where exact affected identity was already known;
- loss of active session/search/filter/tab/selection/modal state after an event.

The required proof is:

**one committed event -> one exact affected projection update -> existing session preserved -> sleep.**

## Locked architecture summary

**DB/Warehouse = authority.**

**Existing event handoff = committed-change signal.**

**Browser session = presentation state.**

**Exact record = unit of event-driven UI change.**

**No event = no work.**

**No polling. No rebuild. No broad reread. No parallel path.**
