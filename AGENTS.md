# BT38 Operational Governance Rules

BT38 must follow one clear path:

Settings Control Center
→ BT38 Command Center
→ Governance Guard
→ Queue / Scheduler
→ Runtime Services
→ Logging + Audit

No deploy without approval.
No production secrets changed.
No duplicate routes.
No direct marketplace push/sync/import from pages long term.
Warehouse is source of truth.
FBA is read-only.
FBM is warehouse-authoritative.
Reverse sync is disabled by default.
Use existing logging: SystemEvent, ConfigChangeLog, SystemConfig, SystemLog.
Every change must follow: audit, backup, replace full block, verify, syntax check, git diff, no deploy, approval.
Current approved scope: Settings Control Center, Command Engine, and the existing governed eBay webhook route's GET destination-verification challenge only. This approval does not extend to POST execution, marketplace push, sync, import, scheduler, or broader webhook runtime changes.
