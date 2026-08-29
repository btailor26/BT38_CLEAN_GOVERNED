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
Do not restore random backup files. Only restore from a verified rollback point already present in GitHub.

## Current recovery commits

Known stabilization commits:
- 1a847f6 BT38 stabilization checkpoint - compact warehouse live, legacy routes isolated, backup structure organised
- 1c26d28 Finalize legacy route isolation and cleanup

## Current working rule

Before any future route/template change:
1. Prove the file is active from the governed GitHub branch.
2. Prove the route is registered from the governed GitHub branch.
3. Prove the production path responds where relevant.
4. Patch the smallest possible scope on `fix/full-system-release-alignment` / PR #528.
5. Verify the exact GitHub diff and required GitHub Actions checks.
6. Do not deploy until the user explicitly approves the exact current PR #528 HEAD SHA.
7. Deploy only through the governed GitHub Actions workflow using the exact PR #528 HEAD and Fly remote builder.
8. Verify production after the approved test deployment.
9. Do not merge PR #528 as part of testing.

The operator PC is not a source, build, test, overlay, or deploy environment for BT38 application files.

## Protected areas

Do not change without explicit approval:
- application shell logo
- sidebar
- top navigation
- nav colours
- approved warehouse shell layout
- active route architecture
- marketplace connection flows

Public Amazon/Appstore branding is a separate public-site concern. Explicitly approved public branding changes must remain isolated from the protected application shell and Warehouse controls.

## Current priority after stabilization

Next priority:
- fix marketplace connection state, starting with eBay connection failure.

No further application-shell layout changes until marketplace connection state is stable. Explicitly approved public Amazon/Appstore compliance work may proceed only in its isolated public-site scope.
