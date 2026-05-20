# BT38 Database Governance

## Production database truth

Production runtime must use:

- Neon PostgreSQL
- DATABASE_URL
- governed Fly deployment

SQLite must never become silent production truth.

## SQLite rules

SQLite is forbidden by default.

SQLite may only be enabled temporarily for isolated emergency local development using:

ALLOW_SQLITE_DEV=true

This must never be enabled silently.

## Runtime protection

If PostgreSQL is expected and SQLite appears unexpectedly:

- STOP
- audit environment
- audit DATABASE_URL
- audit runtime startup path
- audit restored route/code paths

Do not continue operational inventory work until resolved.

## Commercial rule

BT38 is a commercial inventory system.

Inventory truth drift between SQLite and PostgreSQL is considered a critical operational risk.
