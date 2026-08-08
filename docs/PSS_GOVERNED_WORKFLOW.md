# BT38 PSS Governed Workflow

## Core Principle

PSS = Problem -> Solution -> Solve.

BT38 development follows an audit-first governed workflow. No change reaches the governed `main` branch until the problem is proven, the solution is reviewed, the implementation is tested, and the release gate is green.

## Alignment-Only Rule

The active release branch is cumulative. Work on the current release branch must align existing features so they operate together; it must not replace, roll back, silently remove, bypass, or create a parallel implementation of an already completed feature.

When correcting a fault:

- start from the current branch head, not an older branch or isolated historical implementation;
- preserve all current working behaviour unless a specific defect is proven;
- make the smallest alignment required to restore the proven contract;
- do not remove newer Product Linking, Warehouse, marketplace, MCF, runtime, webhook, or protection behaviour merely to make one focused test pass;
- do not use a rollback as the solution when the requirement is alignment;
- do not introduce a second writer, second relationship authority, second webhook path, or duplicate runtime path;
- if a proposed patch removes unrelated current-branch logic, stop and narrow the patch before applying it.

The objective of the release branch is one integrated system in which all completed features continue to run together.

## Current-Branch Testing Rule

All release testing must be performed against the current release branch with all current features present.

A focused fix tested only on an older branch, isolated branch, stripped-down environment, or historical SHA does not prove release readiness. The proposed fix must first be aligned onto the current release branch and then tested there.

If a test fails, continue diagnosis and alignment on the same current branch. Re-run the affected gate and the relevant cross-system gates until the branch passes. Do not declare the problem solved from a narrow unit test if the complete runtime path has not been proven.

For marketplace/webhook changes, proof must preserve and test the complete current path, including where applicable:

- public endpoint/challenge handling;
- immutable webhook capture;
- marketplace/store resolution;
- governed execution routing;
- idempotency/retry handling;
- MarketplaceOrder/canonical database persistence;
- Warehouse stock authority and group propagation;
- Product Linking relationship integrity;
- MCF behaviour;
- FBA/AFN read-only protection;
- recovery/reconcile behaviour;
- existing Amazon and eBay paths so a fix for one marketplace does not break the other.

A challenge-response test alone is not proof that an order webhook works. Where practical, the final gate must prove the external event reaches the deployed application and follows the governed path into the connected database.

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

Rule: smallest targeted alignment; preserve existing working paths unless an exact defect is proven and an explicit replacement is approved.

### Solve

Goal: implement and prove the solution works.

Tools and responsibilities:
- Codex/development tooling: implementation and test creation where used.
- Playwright: browser/UI verification including mobile behaviour.
- GitHub Actions: automatic validation on pushes and pull requests.
- Deployment Readiness: release safety gate.
- Neon Postgres: verify database integrity and schema/data effects.
- Data Analysis: reconciliation, exports and numeric verification where relevant.

Rule: implementation is not complete until it is proven on the current integrated branch.

## Merge Gate

A branch may merge into governed `main` only when all applicable checks pass:

- Problem proven.
- Architecture/solution approved.
- Branch aligned with latest `main`.
- All intended current-branch features remain present.
- No unrelated current behaviour was removed to make the fix pass.
- Working tree clean.
- Python compile passes.
- Automated test suite passes.
- GitHub checks pass.
- Playwright passes for applicable UI/browser behaviour.
- Deployment Readiness passes.
- Neon database audit passes when schema/data access is affected.
- Connected-environment lifecycle testing passes when local tests cannot prove the real database/runtime contract.
- No unexpected database writes.
- Production smoke test passes where required.
- Rollback point is confirmed for safety, but rollback is not used as a substitute for required alignment.
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
- No alignment may create a competing authority for an existing governed path.

## Production Rule

Production is for verified release behaviour only. Development and experimental work remain on branches. Only tested, reviewed and approved merges reach governed `main`, and only governed `main` is eligible for production deployment.
