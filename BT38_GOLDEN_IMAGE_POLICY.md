# BT38 Golden Image Deployment Policy

## Current verified baseline

- Fly application: `bt38-prod`
- Golden image: `registry.fly.io/bt38-prod:deployment-01KXNBXR9ZWZ93Y3NYX610WDMQ`
- Machine version: `v998`

## Mandatory rules

1. Every fix must begin from the source state aligned with the current verified Golden Production Image.

2. Never deploy from an arbitrary, unknown, or mixed-development branch.

3. Each branch and pull request must solve one specific problem only.

4. Do not combine unrelated runtime, warehouse, Product Linking, import, push, database, or UI changes.

5. No UI changes are permitted unless explicitly approved for that exact task.

6. Protected UI areas include:
   - `templates/`
   - `static/css/`
   - `static/js/`

7. Do not change layouts, styling, navigation, buttons, forms, icons, colours, page structure, visible workflows, or existing user-facing behaviour unless explicitly requested.

8. Backend fixes must preserve the current UI and existing behaviour.

9. Existing routes, request formats, responses, integrations, and governed contracts must remain compatible unless an explicit contract change is approved.

10. Every approved change must create a new immutable Fly image.

11. Every new deployment must be audited in production for:
    - Runtime
    - Warehouse
    - Product Linking
    - Imports
    - Push
    - Database activity
    - Fly logs
    - Application errors

12. A new image becomes the Golden Image only after every required audit passes.

13. If an audit fails, immediately restore the previous verified Golden Image.

14. Never remove the previous Golden Image until its replacement has been fully verified.

15. Production is the authoritative environment.

16. Do not redesign, refactor, relocate, rename, replace, or optimise working functionality unless explicitly required by the current task.

17. Audit first. Make no assumptions about production behaviour.

18. The original verified image must remain available as a permanent recovery baseline:
    `registry.fly.io/bt38-prod:deployment-01KXNBXR9ZWZ93Y3NYX610WDMQ`
