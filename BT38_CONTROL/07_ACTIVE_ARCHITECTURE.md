# BT38 ACTIVE ARCHITECTURE

## Current confirmed active production path

Production app:
- Fly app: bt38-prod
- Public URL: https://bt38-prod.fly.dev

Active Flask entry:
- app.py

Active route system:
- routes.py

Active blueprint registration:
- app.py imports: from routes import bp as routes_bp
- app.py registers: app.register_blueprint(routes_bp)

Active warehouse page:
- Route: /warehouse
- Function: routes.warehouse
- Template: templates/warehouse.html

Active inventory page:
- Route: /inventory
- Function: routes.inventory
- Template: templates/inventory.html

## Confirmed compact warehouse layout

The deployed warehouse layout is the compact Master Stock version.

Required markers:
- Master Stock
- Warehouse Truth
- SKU / FNSKU
- Shipping Source
- Group Source
- Listing Status
- Inventory Value

## Legacy route system

Legacy/dead route file:
- routes_clean.py

Current status:
- Not active.
- Not imported by app.py.
- Not registered as a blueprint.
- Moved to: LEGACY_ROUTE_SYSTEMS/routes_clean.py

Rule:
Do not edit or restore from routes_clean.py unless explicitly approved after audit.

## Backup structure

Backups are organized under:
- _bt38_backups/LEGACY_ROUTE_BACKUPS/
- _bt38_backups/WAREHOUSE_LAYOUT_BACKUPS/
- _bt38_backups/BROKEN_STATES/
- _bt38_backups/VERIFIED_ROLLBACKS/
- _bt38_backups/TEMP_EXPERIMENTS/

Rule:
Do not restore random backup files. Only restore from a verified rollback point.

## Current recovery commits

Known stabilization commits:
- 1a847f6 BT38 stabilization checkpoint - compact warehouse live, legacy routes isolated, backup structure organised
- 1c26d28 Finalize legacy route isolation and cleanup

## Current working rule

Before any future route/template change:
1. Prove the file is active.
2. Prove the route is registered.
3. Prove the production path responds.
4. Patch the smallest possible scope.
5. Commit locally.
6. Deploy only after approval.
7. Verify production after deploy.

## Protected areas

Do not change without explicit approval:
- logo
- sidebar
- top navigation
- nav colours
- approved warehouse shell layout
- active route architecture
- marketplace connection flows

## Current priority after stabilization

Next priority:
- fix marketplace connection state, starting with eBay connection failure.

No further layout changes until marketplace connection state is stable.
