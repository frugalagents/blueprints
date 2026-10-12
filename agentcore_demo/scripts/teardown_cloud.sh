#!/usr/bin/env bash
#
# teardown_cloud.sh — tear down ONLY the Advisor Copilot CLOUD layer.
#
# Deletes exactly four resources created by infra/12_cognito.py + infra/13_amplify.py:
#   1. the Amplify Hosting app      (advisor-copilot-ui)
#   2. the Cognito User Pool        (advisor-copilot-users)
#   3. the Cognito Identity Pool    (advisor_copilot_identity)
#   4. the IAM authenticated role   (advisor-copilot-webapp-role)
#      (+ its paired zero-permission unauthenticated role advisor-copilot-webapp-unauth-role)
#
# It reads the resource ids from the gitignored infra/outputs.json. It does NOT
# touch — and contains NO reference to deleting — the AgentCore backend
# (runtime / gateway / memory / guardrail / registry / evaluations / policy) or
# the BFF, and it NEVER re-runs infra/01_guardrail.py.
#
# Usage:
#   ./scripts/teardown_cloud.sh            # prints the plan, then prompts to confirm
#   ./scripts/teardown_cloud.sh --yes      # skip the interactive prompt
#   ./scripts/teardown_cloud.sh --dry-run  # print the plan only, delete nothing
#
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUTS="${REPO_ROOT}/infra/outputs.json"

AUTH_ROLE_NAME="advisor-copilot-webapp-role"
AUTH_ROLE_POLICY_NAME="advisor-copilot-webapp-policy"
UNAUTH_ROLE_NAME="advisor-copilot-webapp-unauth-role"
UNAUTH_ROLE_POLICY_NAME="advisor-copilot-webapp-unauth-deny"

DRY_RUN=0
ASSUME_YES=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --yes|-y)  ASSUME_YES=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

if [[ ! -f "$OUTPUTS" ]]; then
  echo "ERROR: ${OUTPUTS} not found; nothing to tear down." >&2
  exit 1
fi

# Read an id from outputs.json; empty string if absent.
read_output() {
  python3 -c "import json,sys;print(json.load(open('${OUTPUTS}')).get('$1',''))"
}

AMPLIFY_APP_ID="$(read_output amplify_app_id)"
USER_POOL_ID="$(read_output cognito_user_pool_id)"
IDENTITY_POOL_ID="$(read_output cognito_identity_pool_id)"

echo "=============================================================="
echo " Advisor Copilot CLOUD teardown (region ${REGION})"
echo " This will DELETE ONLY the following cloud resources:"
echo "   - Amplify app        : ${AMPLIFY_APP_ID:-<none>}"
echo "   - Cognito User Pool  : ${USER_POOL_ID:-<none>}"
echo "   - Cognito Id Pool    : ${IDENTITY_POOL_ID:-<none>}"
echo "   - IAM role           : ${AUTH_ROLE_NAME}"
echo "   - IAM role           : ${UNAUTH_ROLE_NAME}"
echo
echo " It will NOT touch the AgentCore backend (runtime/gateway/memory/"
echo " guardrail/registry/evaluations/policy) or the BFF, and it will NOT"
echo " run infra/01_guardrail.py."
echo "=============================================================="

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "(--dry-run) No resources deleted."
  exit 0
fi

if [[ "$ASSUME_YES" -ne 1 ]]; then
  read -r -p "Proceed with deletion? [y/N] " reply
  if [[ ! "$reply" =~ ^[Yy]$ ]]; then
    echo "Aborted; nothing deleted."
    exit 0
  fi
fi

# 1. Amplify app (deletes its branches + deployments with it).
if [[ -n "$AMPLIFY_APP_ID" ]]; then
  echo "Deleting Amplify app ${AMPLIFY_APP_ID}..."
  aws amplify delete-app --region "$REGION" --app-id "$AMPLIFY_APP_ID" \
    && echo "  deleted." || echo "  (already gone or delete failed; continuing)"
fi

# 2. Cognito Identity Pool.
if [[ -n "$IDENTITY_POOL_ID" ]]; then
  echo "Deleting Cognito identity pool ${IDENTITY_POOL_ID}..."
  aws cognito-identity delete-identity-pool --region "$REGION" \
    --identity-pool-id "$IDENTITY_POOL_ID" \
    && echo "  deleted." || echo "  (already gone or delete failed; continuing)"
fi

# 3. Cognito User Pool (delete the app-client-bearing pool outright).
if [[ -n "$USER_POOL_ID" ]]; then
  echo "Deleting Cognito user pool ${USER_POOL_ID}..."
  aws cognito-idp delete-user-pool --region "$REGION" \
    --user-pool-id "$USER_POOL_ID" \
    && echo "  deleted." || echo "  (already gone or delete failed; continuing)"
fi

# 4. IAM roles — delete inline policy FIRST, then the role.
delete_role() {
  local role="$1" policy="$2"
  if aws iam get-role --role-name "$role" >/dev/null 2>&1; then
    echo "Deleting inline policy ${policy} from ${role}..."
    aws iam delete-role-policy --role-name "$role" --policy-name "$policy" \
      >/dev/null 2>&1 || true
    echo "Deleting IAM role ${role}..."
    aws iam delete-role --role-name "$role" \
      && echo "  deleted." || echo "  (delete failed; continuing)"
  else
    echo "IAM role ${role} not found; skipping."
  fi
}

delete_role "$AUTH_ROLE_NAME" "$AUTH_ROLE_POLICY_NAME"
delete_role "$UNAUTH_ROLE_NAME" "$UNAUTH_ROLE_POLICY_NAME"

echo "Cloud teardown complete. The AgentCore backend and BFF are untouched."
