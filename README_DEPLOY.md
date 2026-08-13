# BT38 Governed Production Deployment

BT38 has one production deployment path:

`exact GitHub commit → GitHub Actions audit → Fly remote builder → bt38-prod`

The operator's PC is never an application source, build context, test runtime,
overlay source, or deployment machine. Do not clone BT38 to a PC for deployment,
copy files from a PC into an image, or run `fly deploy` against PC files.

## Required controls

- Deployment is manual and requires explicit operator approval.
- The exact GitHub commit SHA must be supplied and must equal the workflow SHA.
- The deployment workflow compiles the production entry points and runs the
  deployment contract tests before Fly is contacted.
- Source files are checked for null-byte corruption before deployment.
- Fly builds remotely from the exact GitHub Actions checkout.
- The target is always `bt38-prod`.
- The workflow does not merge a branch.
- Production secrets remain in GitHub/Fly secrets and must never be copied to a
  PC or committed to GitHub.

## Deploy from GitHub

1. Open the repository on GitHub.
2. Open **Actions → Governed Fly Deployment**.
3. Select **Run workflow** on the branch containing the approved commit.
4. Enter the exact commit SHA shown on that branch.
5. Enter `DEPLOY_GITHUB_COMMIT_TO_BT38_PROD` in the approval field.
6. Run the workflow and wait for both `audit` and `deploy` to pass.
7. Verify the new Fly release and run the approved browser checks.

If the exact commit, audit, source-integrity check, locked dependency install,
or Fly deployment fails, the workflow stops. Do not bypass it with a local
overlay or a direct PC deployment.

## Production verification

Verification is read-only unless a separate test explicitly authorizes a
governed write. Confirm:

- the Fly machine is started;
- the release is complete;
- the application health endpoint responds;
- the deployed UI contains the approved change;
- Product Linking, Warehouse, marketplace writes, and FBA read-only boundaries
  remain aligned.

Rollback is also performed through a separately approved GitHub/Fly production
workflow. Never restore an image or source tree from an operator PC.
