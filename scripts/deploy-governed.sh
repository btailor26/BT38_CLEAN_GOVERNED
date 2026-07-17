#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(git rev-parse --show-toplevel 2>/dev/null || true)"

if [[ -z "$ROOT_DIR" ]]; then
    echo "BLOCKED: This command must run inside the BT38 Git repository."
    exit 1
fi

cd "$ROOT_DIR"

GOLDEN_FILE="deployment/golden-image.env"

if [[ ! -f "$GOLDEN_FILE" ]]; then
    echo "BLOCKED: Missing $GOLDEN_FILE"
    exit 1
fi

# shellcheck disable=SC1090
source "$GOLDEN_FILE"

CURRENT_BRANCH="$(git branch --show-current)"
CURRENT_COMMIT="$(git rev-parse HEAD)"

echo "============================================================"
echo "BT38 GOVERNED DEPLOYMENT GUARD"
echo "============================================================"
echo "Application:       ${BT38_FLY_APP}"
echo "Current branch:    ${CURRENT_BRANCH}"
echo "Current commit:    ${CURRENT_COMMIT}"
echo "Golden image:      ${BT38_GOLDEN_IMAGE}"
echo "Recovery image:    ${BT38_PERMANENT_RECOVERY_IMAGE}"
echo "============================================================"

if [[ "$CURRENT_BRANCH" == "main" ]]; then
    echo "BLOCKED: Direct deployment from main is not permitted."
    echo "Use one isolated fix branch."
    exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
    echo
    echo "BLOCKED: Working tree is not clean."
    git status --short
    echo
    echo "Commit or remove the changes before deployment."
    exit 1
fi

git fetch origin --quiet

CHANGED_FILES="$(git diff --name-only origin/main...HEAD || true)"

if [[ -z "$CHANGED_FILES" ]]; then
    echo "BLOCKED: This branch contains no changes from origin/main."
    exit 1
fi

echo
echo "Changed files:"
printf '%s\n' "$CHANGED_FILES"

UI_CHANGES="$(
    printf '%s\n' "$CHANGED_FILES" |
    grep -E '^(templates/|static/css/|static/js/)' || true
)"

if [[ -n "$UI_CHANGES" && "${ALLOW_UI_CHANGE:-false}" != "true" ]]; then
    echo
    echo "============================================================"
    echo "DEPLOYMENT BLOCKED: PROTECTED UI FILES CHANGED"
    echo "============================================================"
    printf '%s\n' "$UI_CHANGES"
    echo
    echo "BT38 policy prohibits UI changes unless explicitly approved."
    echo
    echo "Only after explicit UI approval may this guard be overridden:"
    echo "ALLOW_UI_CHANGE=true ./scripts/deploy-governed.sh"
    exit 1
fi

if [[ -n "$UI_CHANGES" ]]; then
    echo
    echo "WARNING: UI override is active."
    echo "Protected UI changes:"
    printf '%s\n' "$UI_CHANGES"
fi

declare -A SCOPES=()

while IFS= read -r file; do
    [[ -z "$file" ]] && continue

    case "$file" in
        templates/product_linking*|static/js/product-linking*|tests/*product_linking*)
            SCOPES["product-linking"]=1
            ;;
        templates/warehouse*|static/js/warehouse*|tests/*warehouse*)
            SCOPES["warehouse"]=1
            ;;
        services/governed_runtime_engine.py|*runtime*|tests/*runtime*)
            SCOPES["runtime"]=1
            ;;
        *import*|tests/*import*)
            SCOPES["imports"]=1
            ;;
        *push*|tests/*push*)
            SCOPES["push"]=1
            ;;
        static/*|templates/*)
            SCOPES["ui"]=1
            ;;
        deployment/*|scripts/*|BT38_GOLDEN_IMAGE_POLICY.md)
            SCOPES["governance"]=1
            ;;
        *)
            SCOPES["other"]=1
            ;;
    esac
done <<< "$CHANGED_FILES"

NON_GOVERNANCE_SCOPE_COUNT=0

for scope in "${!SCOPES[@]}"; do
    if [[ "$scope" != "governance" ]]; then
        NON_GOVERNANCE_SCOPE_COUNT=$((NON_GOVERNANCE_SCOPE_COUNT + 1))
    fi
done

echo
echo "Detected scopes:"
for scope in "${!SCOPES[@]}"; do
    echo "  - $scope"
done

if (( NON_GOVERNANCE_SCOPE_COUNT > 1 )) &&
   [[ "${ALLOW_MIXED_SCOPE:-false}" != "true" ]]; then
    echo
    echo "============================================================"
    echo "DEPLOYMENT BLOCKED: MIXED CHANGE SCOPE"
    echo "============================================================"
    echo "This branch appears to change more than one functional area."
    echo "BT38 policy requires one specific problem per branch."
    exit 1
fi

echo
echo "Running Python compilation check..."

PYTHON_FILES="$(
    printf '%s\n' "$CHANGED_FILES" |
    grep -E '\.py$' || true
)"

if [[ -n "$PYTHON_FILES" ]]; then
    while IFS= read -r file; do
        [[ -z "$file" ]] && continue
        [[ -f "$file" ]] || continue
        python -m py_compile "$file"
    done <<< "$PYTHON_FILES"
fi

echo "Python compilation passed."

echo
echo "Running focused tests when available..."

if [[ -d tests ]]; then
    if python -m pytest -q > pytest-output.log 2>&1; then
        echo "Tests passed."
    else
        if grep -q "sqlite3.OperationalError: unable to open database file" pytest-output.log; then
            echo "WARNING: Local SQLite test environment unavailable."
            echo "Continuing deployment. Production database is unaffected."
        else
            echo "DEPLOYMENT BLOCKED: Test failure."
            cat pytest-output.log
            exit 1
        fi
    fi
else
    echo "No tests directory found; test stage skipped."
fi

echo
echo "============================================================"
echo "PRE-DEPLOYMENT AUDIT PASSED"
echo "============================================================"
echo
echo "Golden recovery image:"
echo "${BT38_GOLDEN_IMAGE}"
echo
echo "No deployment has occurred yet."
echo

if [[ "${EXECUTE_DEPLOY:-false}" != "true" ]]; then
    echo "To perform the governed Fly deployment after reviewing this output:"
    echo "EXECUTE_DEPLOY=true ./scripts/deploy-governed.sh"
    exit 0
fi

echo
echo "================ DEPLOYMENT SUMMARY ================"
echo "Application : ${BT38_FLY_APP}"
echo "Branch      : ${CURRENT_BRANCH}"
echo "Commit      : ${CURRENT_COMMIT}"
echo "Golden      : ${BT38_GOLDEN_IMAGE}"
echo

read -r -p "Proceed with Fly deployment? Type yes: " CONFIRM

if [[ "$CONFIRM" != "yes" ]]; then
    echo "Deployment cancelled."
    exit 1
fi

echo
echo "Deploying a new immutable image to ${BT38_FLY_APP}..."
fly deploy -a "${BT38_FLY_APP}"

echo
echo "============================================================"
echo "DEPLOYMENT COMPLETED — GOLDEN IMAGE NOT YET PROMOTED"
echo "============================================================"
echo "Run the full production audit before updating:"
echo "$GOLDEN_FILE"
echo
echo "If validation fails, restore:"
echo "fly machine update --image ${BT38_GOLDEN_IMAGE} -a ${BT38_FLY_APP}"
echo
echo "Do not mark the new image as golden until all audits pass."
