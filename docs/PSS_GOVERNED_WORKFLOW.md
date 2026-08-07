# BT38 PSS Governed Workflow

## Core Principle

PSS = Problem -> Solution -> Solve.

BT38 development follows an audit-first governed workflow. No change reaches the governed `main` branch until the problem is proven, the solution is reviewed, the implementation is tested, and the release gate is green.

## Branch and Deployment Policy

- All development work is performed on branches.
- `main` is the governed release authority.
- Production is deployed only from tested and approved `main`.
- Feature/fix branches may be used for development and testing but are not production authority.
- Branches merge into `main` only after the required PSS checks and release gate pass.
- No direct unreviewed production changes from feature branches.
- Final merge and production deployment require human approval.

## PSS Roles

### Problem

Goal: identify and prove the exact issue before changing code.

Tools and responsibilities:
- ChatGPT/GPT: architecture review, root-cause analysis, risk review.
- GitHub: branch, commit, PR, route and code audit.
- Neon Postgres: schema, relationship, data and query audit.
- Files/specifications: requirements and acceptance criteria.

Rule: audit first; no blind fixes.

### Solution

Goal: design the smallest governed correction.

Tools and responsibilities:
- ChatGPT/GPT: solution design and architecture validation.
- GitHub: review affected code and identify regressions or duplicate paths.
- Files/specifications: update requirements when behaviour changes.

Rule: smallest targeted change; preserve existing working paths unless explicitly approved.

### Solve

Goal: implement and prove the solution works.

Tools and responsibilities:
- Codex/development tooling: implementation and test creation where used.
- Playwright: browser/UI verification including mobile behaviour.
- GitHub Actions: automatic validation on pushes and pull requests.
- Deployment Readiness: release safety gate.
- Neon Postgres: verify database integrity and schema/data effects.
- Data Analysis: reconciliation, exports and numeric verification where relevant.

Rule: implementation is not complete until it is proven.

## Merge Gate

A branch may merge into governed `main` only when all applicable checks pass:

- Problem proven.
- Architecture/solution approved.
- Branch aligned with latest `main`.
- Working tree clean.
- Python compile passes.
- Automated test suite passes.
- GitHub checks pass.
- Playwright passes for applicable UI/browser behaviour.
- Deployment Readiness passes.
- Neon database audit passes when schema/data access is affected.
- No unexpected database writes.
- Production smoke test passes where required.
- Rollback point is confirmed.
- Human approval is given.

No single tool may approve a merge by itself.

## BT38 Authority Rules

- GitHub is the source of truth for code.
- Governed `main` is the release authority.
- Warehouse is the source of truth for inventory.
- Product Linking manages relationships only.
- FBA/AFN remains protected from unauthorized writes.
- Marketplace writes must use governed execution paths.
- No retired/legacy route may become an execution authority.

## Production Rule

Production is for verified release behaviour only. Development and experimental work remain on branches. Only tested, reviewed and approved merges reach governed `main`, and only governed `main` is eligible for production deployment.
