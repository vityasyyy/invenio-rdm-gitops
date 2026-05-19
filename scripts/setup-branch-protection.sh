#!/usr/bin/env bash
set -euo pipefail

if ! command -v gh &>/dev/null; then
  echo "✗ gh CLI is not installed. Install it from https://cli.github.com"
  exit 1
fi

if ! gh auth status &>/dev/null; then
  echo "✗ gh CLI is not authenticated. Run 'gh auth login'"
  exit 1
fi

REPO=$(git remote get-url origin 2>/dev/null | sed -E 's|.*[:/]([^/]+/[^/]+)(\.git)?$|\1|' || echo "")

if [[ -z "$REPO" ]]; then
  REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || echo "")
fi

if [[ -z "$REPO" ]]; then
  echo "✗ Could not determine repository. Run this from inside a git repo or set GIT_REMOTE"
  exit 1
fi

echo "✓ Setting up branch protection for main in $REPO"

gh api \
  --method PUT \
  "/repos/$REPO/branches/main/protection" \
  --input - <<'EOF'
{
  "required_status_checks": {
    "strict": false,
    "contexts": [
      "Validate YAML Syntax",
      "Validate Kustomize Builds",
      "Validate ArgoCD Applications"
    ]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": false,
    "require_code_owner_reviews": false,
    "required_approving_review_count": 1
  },
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false
}
EOF

echo "✓ Branch protection configured for main"
echo "  - 1 required approval"
echo "  - Status checks: Validate YAML Syntax, Validate Kustomize Builds, Validate ArgoCD Applications"
echo "  - Linear history required"
echo "  - Force pushes disallowed"
echo "  - Not requiring branch up-to-date before merging"