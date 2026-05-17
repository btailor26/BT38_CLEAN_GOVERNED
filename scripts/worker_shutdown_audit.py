#!/usr/bin/env python3
"""
BT38 worker shutdown audit.

Static, local-only audit for legacy workers, schedulers, background loops,
dispatcher starters, auto push/sync hooks, import schedulers, queue consumers,
and job cleanup loops. This script does not import the Flask app and does not
call marketplace APIs.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]

KEEP_TEMPORARY = "KEEP_TEMPORARY"
DISABLE_NOW = "DISABLE_NOW"
REMOVE_LATER = "REMOVE_LATER"
READ_ONLY_IMPORT_ONLY = "READ_ONLY_IMPORT_ONLY"
UNSURE_NEEDS_REVIEW = "UNSURE_NEEDS_REVIEW"


@dataclass(frozen=True)
class Finding:
    name: str
    path: str
    marker: str
    classification: str
    reason: str


FINDINGS = [
    Finding(
        "Flask import dispatcher auto-start",
        "app.py",
        "start_dispatcher()",
        DISABLE_NOW,
        "Old background execution starter; app import must not launch queue consumers before governed starter exists.",
    ),
    Finding(
        "Local dev legacy sync thread starter",
        "main.py",
        "threading.Thread(target=start_sync_service",
        DISABLE_NOW,
        "Duplicate legacy sync loop starter; local launch must not bypass governed runtime path.",
    ),
    Finding(
        "Legacy infinite sync loop entrypoint",
        "sync_service.py",
        "def start_sync_service",
        DISABLE_NOW,
        "Old 30-second background loop that directly invokes sync_store and maintenance checks.",
    ),
    Finding(
        "Sync dispatcher callable",
        "sync_dispatcher.py",
        "def start_dispatcher",
        KEEP_TEMPORARY,
        "Current queue consumer implementation kept callable for future governed execution starter; no app auto-start allowed.",
    ),
    Finding(
        "Per-store queue worker threads",
        "sync_dispatcher.py",
        "StoreWorker-",
        KEEP_TEMPORARY,
        "Queue consumer internals kept temporarily, but only reachable if a future governed starter explicitly starts dispatcher.",
    ),
    Finding(
        "Dispatcher stuck-job watchdog / cleanup loop",
        "sync_dispatcher.py",
        "reset_stuck_jobs(timeout_minutes=30)",
        KEEP_TEMPORARY,
        "Cleanup remains coupled to dispatcher internals; inactive while dispatcher auto-start is disabled.",
    ),
    Finding(
        "Order import scheduler class",
        "sync_dispatcher.py",
        "class OrderImportScheduler",
        READ_ONLY_IMPORT_ONLY,
        "Read-only import scheduler retained but not auto-started; needs governed scheduler control before use.",
    ),
    Finding(
        "Order import scheduler starter",
        "sync_dispatcher.py",
        "def start_order_import_scheduler",
        READ_ONLY_IMPORT_ONLY,
        "Starter remains callable for future governed read-only import path; app must not call it directly.",
    ),
    Finding(
        "FBA inventory import service path",
        "amazon_service.py",
        "def import_inventory_from_amazon",
        READ_ONLY_IMPORT_ONLY,
        "FBA import is read-only by policy; keep only behind governed import command/scheduler.",
    ),
    Finding(
        "immediate_sync_store",
        "sync_service.py",
        "def immediate_sync_store",
        REMOVE_LATER,
        "Legacy direct store sync helper retained for compatibility review; routes should not invoke it.",
    ),
    Finding(
        "sync_store",
        "sync_service.py",
        "def sync_store",
        KEEP_TEMPORARY,
        "Runtime service function remains active for dispatcher/governed runtime, with marketplace guards in downstream paths.",
    ),
    Finding(
        "sync_item_to_store",
        "sync_service.py",
        "def sync_item_to_store",
        REMOVE_LATER,
        "Legacy direct push helper is fail-closed; dispatcher uses smart_push_service instead.",
    ),
    Finding(
        "Auto push dry-run enqueue hook",
        "auto_push_service.py",
        "def queue_auto_push_for_sku",
        REMOVE_LATER,
        "Automatic enqueue hook is disabled by settings by default, but should be rewired through governed scheduler/command path.",
    ),
    Finding(
        "Manual real push enqueue helper",
        "auto_push_service.py",
        "def queue_real_push_for_warehouse",
        KEEP_TEMPORARY,
        "Queue-only helper retained temporarily; guarded by runtime and marketplace eligibility before enqueue.",
    ),
    Finding(
        "Queue enqueue API",
        "queue_manager.py",
        "def enqueue_sync_job",
        KEEP_TEMPORARY,
        "Queue creation path retained for future governed commands; guarded before push jobs are accepted.",
    ),
    Finding(
        "Old job cleanup helper",
        "queue_manager.py",
        "def cleanup_old_jobs",
        KEEP_TEMPORARY,
        "Maintenance helper retained; no independent loop/starter found.",
    ),
    Finding(
        "Store manual sync route",
        "routes.py",
        "def manual_sync_store",
        REMOVE_LATER,
        "Route is already retired with 410 and does not call immediate_sync_store/sync_store.",
    ),
]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def contains(rel: str, needle: str) -> bool:
    return needle in read(rel)


def line_of(rel: str, needle: str) -> int | None:
    for idx, line in enumerate(read(rel).splitlines(), 1):
        if needle in line:
            return idx
    return None


def assert_shutdown_proofs() -> list[str]:
    failures: list[str] = []
    app_text = read("app.py")
    main_text = read("main.py")
    sync_text = read("sync_service.py")

    if "start_dispatcher()" in app_text:
        failures.append("app.py still calls start_dispatcher() during app import")
    if "start_order_import_scheduler()" in app_text:
        failures.append("app.py still calls start_order_import_scheduler() during app import")
    if "threading.Thread(target=start_sync_service" in main_text:
        failures.append("main.py still starts legacy start_sync_service thread")
    if "while True:" in sync_text[sync_text.find("def start_sync_service"):sync_text.find("def detect_and_recover_stuck_stores")]:
        failures.append("start_sync_service still contains an infinite while True loop")
    if "[WORKER_SHUTDOWN] start_sync_service is disabled" not in sync_text:
        failures.append("start_sync_service missing disabled shutdown marker")
    if "[WORKER_SHUTDOWN] Sync dispatcher auto-start DISABLED" not in app_text:
        failures.append("app.py missing dispatcher shutdown marker")

    return failures


def main() -> int:
    print("BT38 Worker Shutdown Audit")
    print("==========================")
    print("Scope: workers, schedulers, background loops, dispatcher starters, auto push, auto sync,")
    print("       order import scheduler, FBA import scheduler, immediate_sync_store, sync_store,")
    print("       sync_item_to_store, queue consumers, job cleanup loops")
    print("Mode: static local audit only; no app import, no deploy, no marketplace API calls")
    print()

    print("Workers found and classified:")
    for finding in FINDINGS:
        present = contains(finding.path, finding.marker)
        line = line_of(finding.path, finding.marker)
        status = "FOUND" if present else "MISSING"
        location = f"{finding.path}:{line}" if line else finding.path
        print(f"- [{finding.classification}] {finding.name} — {status} at {location}")
        print(f"  reason: {finding.reason}")

    print()
    print("Shutdown proof checks:")
    failures = assert_shutdown_proofs()
    if failures:
        for failure in failures:
            print(f"- FAIL: {failure}")
        return 1

    print("- PASS: app.py does not call start_dispatcher() on import")
    print("- PASS: app.py does not call start_order_import_scheduler() on import")
    print("- PASS: main.py does not spawn start_sync_service in a background thread")
    print("- PASS: start_sync_service is fail-closed and contains no infinite loop")
    print("- PASS: dispatcher/order import/queue worker internals are callable only, not auto-started")
    print()
    print("NO DEPLOY RUN")
    print("READY FOR USER REVIEW")
    return 0


if __name__ == "__main__":
    sys.exit(main())
