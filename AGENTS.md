# BT38 Operational Governance Rules

BT38 must follow one clear path:

Settings Control Center
→ BT38 Command Center
→ Governance Guard
→ Queue / Scheduler
→ Runtime Services
→ Logging + Audit

No deploy without approval.
Production code and deployment context must come only from an exact GitHub
commit. Never clone, copy, overlay, build, test, or deploy application files
from an operator's PC. The operator's PC may open GitHub Actions and inspect
Fly, but it is never a BT38 source or build machine.
All production deployments must use the manually approved GitHub Actions
workflow and Fly remote builder. Direct `fly deploy` from an operator PC is
prohibited.
No production secrets changed.
No duplicate routes.
No direct marketplace push/sync/import from pages long term.
Warehouse is source of truth.
FBA is read-only.
FBM is warehouse-authoritative.
Reverse sync is disabled by default.
Use existing logging: SystemEvent, ConfigChangeLog, SystemConfig, SystemLog.
Every change must follow: audit, backup, replace full block, verify, syntax check, git diff, no deploy, approval.

Current approved branch scope for `fix/full-system-release-alignment` includes the governed event-driven Amazon/eBay order and webhook execution already under test, Warehouse/Product Linking authority alignment, MCF/FBA read-only handling, FBM/Packlink shipping, exact marketplace destination hydration, marketplace dispatch confirmation, shipment/bell audit visibility, tracking journey display from provider/platform data, and standalone manual shipping. This approval is for audit, contract testing, and explicitly approved test deployment of an exact GitHub commit only; it does not authorize merge or an unreviewed production source change.
