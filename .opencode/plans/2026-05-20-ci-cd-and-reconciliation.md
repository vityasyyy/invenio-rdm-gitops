# Cluster Reconciliation + CI/CD Pipeline Plan

> **Status: FINAL PLAN — Ready for execution after review.**

---

## Phase A: Cluster Reconciliation (CRITICAL — execute first)

5 issues from the 15-commit push need fixing before any CI/CD work:

### A1: Add `AppProject` to `infra` project's `clusterResourceWhitelist`

**File:** `argocd/projects/infra-project.yaml`

Add after line 69 (after `IngressClass`):
```yaml
    - group: argoproj.io
      kind: AppProject
```

This allows `argocd-self` (project: infra) to create/manage AppProject resources like `invenio`.

### A2: Fix Discord bridge image — replace nonexistent `ghcr.io/sergelogvinov/alertmanager-discord:latest`

**File:** `k8s/infra/monitoring/alertmanager-discord-deployment.yaml`

The image `ghcr.io/sergelogvinov/alertmanager-discord:latest` doesn't exist (GHCR returns 401/DENIED). Replace with `benjojo/alertmanager-discord:latest` (200K+ pulls on Docker Hub, confirmed working).

**Changes needed:**
1. **Image:** `ghcr.io/sergelogvinov/alertmanager-discord:latest` → `benjojo/alertmanager-discord:latest`
2. **Env var:** `DISCORD_WEBHOOK_URL` → `DISCORD_WEBHOOK` (benjojo uses `DISCORD_WEBHOOK`)
3. **Remove CLI arg** `-web.listen-address=0.0.0.0:9093` — benjojo uses env var `LISTEN_ADDRESS` instead
4. **Add env var** `LISTEN_ADDRESS: "0.0.0.0:9093"` (benjojo defaults to `127.0.0.1:9094`)
5. **SealedSecret key rename:** The SealedSecret has `DISCORD_WEBHOOK_URL` — need to re-seal with `DISCORD_WEBHOOK` key, OR keep `DISCORD_WEBHOOK_URL` as env var and add a second env var `DISCORD_WEBHOOK` referencing the same secret key.

**Simplest approach:** Keep the SealedSecret as-is, and add both env vars:
```yaml
env:
  - name: DISCORD_WEBHOOK
    valueFrom:
      secretKeyRef:
        name: alertmanager-discord-webhook
        key: DISCORD_WEBHOOK_URL
  - name: LISTEN_ADDRESS
    value: "0.0.0.0:9093"
```

The SealedSecret's encrypted data key is `DISCORD_WEBHOOK_URL` — benjojo reads `DISCORD_WEBHOOK` env var. By mapping `key: DISCORD_WEBHOOK_URL` to `name: DISCORD_WEBHOOK`, we don't need to re-seal the secret.

**Note:** benjojo's container listens on any path (no `/alerts` endpoint needed), so the existing Alertmanager config `http://alertmanager-discord:9093/alerts` will work fine — the path is just ignored.

### A3: Apply AppProjects to cluster

```bash
# 1. Apply infra project (adds AppProject to whitelist + grafana helm repo)
kubectl apply -f argocd/projects/infra-project.yaml

# 2. Apply invenio project (creates it for the first time)
kubectl apply -f argocd/projects/invenio-project.yaml
```

### A4: Force-sync all broken ArgoCD apps

```bash
# Sync argocd-self first (picks up the invenio project)
argocd app sync argocd-self

# Sync apps that were blocked by missing project
argocd app sync invenio-postgresql invenio-redis invenio-opensearch invenio-bootstrap

# Sync apps blocked by missing Helm repo
argocd app sync loki

# Re-sync monitoring-extras (Discord bridge image fix)
argocd app sync monitoring-extras
```

### A5: Verify cluster health

```bash
kubectl get pods -A | grep -v Running | grep -v Completed
kubectl get appproj -n argocd
argocd app list
```

All apps should show `Synced` and `Healthy`.

---

## Phase B: CI/CD Pipeline Rewrite

### B1: Create `scripts/ci-render-manifests.sh`

Renders all kustomize directories and Helm charts for downstream validation.

**Kustomize directories** (11 total):
```
k8s/infra/argocd
k8s/infra/argocd-image-updater
k8s/infra/minio
k8s/infra/monitoring
k8s/infra/sealed-secrets
k8s/infra/security
k8s/infra/velero
k8s/apps/invenio
k8s/apps/invenio-deps/postgresql
k8s/apps/invenio-deps/opensearch/manifests
k8s/apps/invenio-deps/redis/manifests
external-lb/k8s
```

**Helm charts** (7 total):
```
traefik       → https://traefik.github.io/charts, chart: traefik, v39.0.6, values: k8s/infra/traefik/values.yaml
minio         → https://charts.min.io/, chart: minio, v5.4.0, values: k8s/infra/minio/values.yaml
velero        → https://vmware-tanzu.github.io/helm-charts, chart: velero, v11.4.0, values: k8s/infra/velero/values.yaml
monitoring    → https://prometheus-community.github.io/helm-charts, chart: kube-prometheus-stack, v69.6.0, values: k8s/infra/monitoring/values.yaml
loki          → https://grafana.github.io/helm-charts, chart: loki, v6.24.0, values: k8s/infra/loki/values.yaml
opensearch    → https://opensearch-project.github.io/helm-charts/, chart: opensearch, v2.32.0, values: inline in ArgoCD app
cloudnative-pg → https://cloudnative-pg.github.io/charts, chart: cloudnative-pg, v0.23.0, values: inline in ArgoCD app
```

Script outputs all manifests to `rendered/` dir for kubeconform/selector validation.

### B2: Create `scripts/ci-validate-selectors.sh`

Cross-references rendered manifests:
- Service selectors ↔ Deployment/StatefulSet/DaemonSet pod labels
- HPA scaleTargetRef ↔ Deployment name match
- PDB selector ↔ Deployment pod labels
- NetworkPolicy podSelector ↔ target pod labels (namespace-scoped)

Catches silent failures (e.g., HPA targeting `app: invenio-web` when deployment uses `app.kubernetes.io/name: invenio-web`).

### B3: Rewrite `.github/workflows/validate-infra.yaml`

Single job, runs on PR to `main`:

| Step | Tool | What |
|---|---|---|
| 1 | checkout | Checkout code |
| 2 | pip | Install yamllint |
| 3 | helm | Add repos, update |
| 4 | kustomize | Install v5.4.1 |
| 5 | kubeconform | Install latest |
| 6 | bash | Run `ci-render-manifests.sh` |
| 7 | bash | Run `ci-validate-selectors.sh` |
| 8 | kubeconform | Validate rendered manifests |
| 9 | yamllint | Lint source YAML |
| 10 | bash | Scan for PLACEHOLDER/TODO in SealedSecrets |
| 11 | bash | Validate SealedSecret encryptedData starts with `Ag` |
| 12 | bash | Validate ArgoCD app source paths exist |
| 13 | bash | Validate sync wave ordering |
| 14 | gitleaks | Scan for leaked secrets |

Paths filter triggers: `argocd/**`, `k8s/**`, `external-lb/**`, `scripts/**`, `.github/workflows/**`

### B4: Modify `.github/workflows/deploy-verify.yaml`

Add after ArgoCD health check:
1. Install `curl`
2. Smoke test: `curl -sf https://invenio.vityasy.me/api/health || true`
3. Report smoke test result (non-blocking — don't fail the workflow if smoke test fails)

Note: The smoke test should be non-blocking initially, since the site may not be accessible from GitHub Actions (Cloudflare tunnel, private network). If it can't reach the endpoint, just log a warning.

### B5: Fix `.pre-commit-config.yaml`

Current issues:
1. **kustomize hook fails if binary not in PATH** — `system` language doesn't check for binary existence
2. **No SealedSecret placeholder check**
3. **No kubeconform validation**

Fix:
1. Add `entry: bash` with `[ -x "$(command -v kustomize)" ] || exit 0` guard
2. Add local hook: `ci-validate-selectors.sh` (runs on kustomization.yaml changes)
3. Remove the broken kustomize-build hook and replace with a render + validate hook
4. Add a placeholder detection hook for SealedSecret files

### B6: Create `scripts/setup-branch-protection.sh`

Uses `gh` CLI:
```bash
gh api repos/{owner}/{repo}/branches/main/protection \
  --method PUT \
  --field required_pull_request_reviews='{"required_approving_review_count":1}' \
  --field required_status_checks='{"strict":true,"contexts":["Validate YAML Syntax","Validate Kustomize Builds","Validate ArgoCD Applications"]}' \
  --field enforce_admins=true \
  --field allow_force_pushes=false
```

---

## Phase C: Create PR and Merge

All Phase B changes go through a PR to dogfood the new pipeline:

```bash
git checkout -b ci/cd-pipeline-upgrade
# Make all changes
git push origin ci/cd-pipeline-upgrade
gh pr create --title "ci: comprehensive pipeline upgrade" --body "..."
```

After merge, run `scripts/setup-branch-protection.sh`.

---

## Phase D: Minor Cleanup

- D1: Add `rendered/` to `.gitignore`
- D2: Update `.yamllint` to ignore Helm value files (already covered by `values.yaml` pattern)

---

## Execution Order

1. **Phase A** (A1-A5) — Fix cluster issues first
2. **Phase B** (B1-B6) — Create CI scripts and rewrite workflows
3. **Phase C** — PR branch, test, merge, set up branch protection
4. **Phase D** — Minor cleanup