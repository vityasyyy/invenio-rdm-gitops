# Cluster Health Fixes & Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all ArgoCD out-of-sync issues caused by misconfigured PDB selectors, probe configurations, and HPA replica drift, then apply best-practice improvements identified in the cluster audit.

**Architecture:** Direct YAML manifest edits across 12+ files in the gitops repo, each task targeting a specific misconfiguration. Changes will be picked up by ArgoCD auto-sync.

**Tech Stack:** Kubernetes YAML, ArgoCD, Kustomize

---

## Part A: Critical Fixes (Causing ArgoCD Out-of-Sync / App Degradation)

### Task 1: Fix PDB Label Selectors

**Files:**
- Modify: `k8s/apps/invenio/invenio-pdb.yaml`

The PDB selectors use `app: invenio-web` / `app: invenio-worker` but the pods carry `app.kubernetes.io/name: invenio-web` / `app.kubernetes.io/name: invenio-worker`. This makes both PDBs match zero pods, rendering them non-functional and causing ArgoCD to report degraded status.

- [ ] **Step 1: Update PDB selectors**

Change:
```yaml
    matchLabels:
      app: invenio-web
```
To:
```yaml
    matchLabels:
      app.kubernetes.io/name: invenio-web
```

And:
```yaml
    matchLabels:
      app: invenio-worker
```
To:
```yaml
    matchLabels:
      app.kubernetes.io/name: invenio-worker
```

- [ ] **Step 2: Verify the change locally**

Run: `kubectl apply --dry-run=client -f k8s/apps/invenio/invenio-pdb.yaml`
Expected: No validation errors

- [ ] **Step 3: Commit**

```bash
git add k8s/apps/invenio/invenio-pdb.yaml
git commit -m "fix: correct PDB selectors to match deployment pod labels"
```

---

### Task 2: Align Deployment Replicas with HPA minReplicas

**Files:**
- Modify: `k8s/apps/invenio/invenio-deployment.yaml`
- Modify: `k8s/apps/invenio/invenio-worker-deployment.yaml`

The HPA sets `minReplicas: 2` for both web and worker, but the deployments have `replicas: 1`. This creates permanent ArgoCD drift: ArgoCD wants replicas=1 (from git), HPA enforces replicas=2.

- [ ] **Step 1: Update web deployment replicas**

In `k8s/apps/invenio/invenio-deployment.yaml`, change `replicas: 1` to `replicas: 2`.

- [ ] **Step 2: Update worker deployment replicas**

In `k8s/apps/invenio/invenio-worker-deployment.yaml`, change `replicas: 1` to `replicas: 2`.

- [ ] **Step 3: Commit**

```bash
git add k8s/apps/invenio/invenio-deployment.yaml k8s/apps/invenio/invenio-worker-deployment.yaml
git commit -m "fix: align deployment replicas with HPA minReplicas to eliminate ArgoCD drift"
```

---

### Task 3: Fix HTTP Readiness Probe for Invenio Web

**Files:**
- Modify: `k8s/apps/invenio/invenio-deployment.yaml`

The `/api/health` endpoint returns HTTP 400 during app initialization. Startup and liveness probes are already TCP (correct), but readiness uses HTTP with a 60s delay. Switch to TCP for consistency.

- [ ] **Step 1: Change readiness probe from HTTP to TCP**

Replace:
```yaml
          readinessProbe:
            httpGet:
              path: /api/health
              port: http
            initialDelaySeconds: 60
            periodSeconds: 10
            timeoutSeconds: 10
            failureThreshold: 3
```
With:
```yaml
          readinessProbe:
            tcpSocket:
              port: http
            initialDelaySeconds: 30
            periodSeconds: 10
            timeoutSeconds: 5
            failureThreshold: 3
```

- [ ] **Step 2: Verify the file is valid YAML**

Run: `python3 -c "import yaml; yaml.safe_load(open('k8s/apps/invenio/invenio-deployment.yaml')); print('YAML valid')"`
Expected: "YAML valid"

- [ ] **Step 3: Commit**

```bash
git add k8s/apps/invenio/invenio-deployment.yaml
git commit -m "fix: switch web readiness probe to TCP to prevent never-Ready pods"
```

---

### Task 4: Add Startup Probe to Worker

**Files:**
- Modify: `k8s/apps/invenio/invenio-worker-deployment.yaml`

The worker has no startup probe. Its liveness probe has a 120s initial delay, but with a 45s timeout and 3 failures, the total grace window is only ~180s. A startup probe with generous threshold gives the worker time.

- [ ] **Step 1: Add startup probe before liveness probe**

Add:
```yaml
          startupProbe:
            exec:
              command:
                - /bin/sh
                - -c
                - celery -A invenio_app.celery inspect ping -d celery@$(hostname) --timeout 30
            initialDelaySeconds: 30
            periodSeconds: 15
            failureThreshold: 12
```

- [ ] **Step 2: Reduce liveness probe initialDelaySeconds**

Change `initialDelaySeconds: 120` to `initialDelaySeconds: 30` in the liveness probe (the startup probe now provides the grace period).

- [ ] **Step 3: Verify the file is valid YAML**

Run: `python3 -c "import yaml; yaml.safe_load(open('k8s/apps/invenio/invenio-worker-deployment.yaml')); print('YAML valid')"`
Expected: "YAML valid"

- [ ] **Step 4: Commit**

```bash
git add k8s/apps/invenio/invenio-worker-deployment.yaml
git commit -m "fix: add startup probe to worker for reliable initialization"
```

---

## Part B: High-Priority Improvements (Security & Consistency)

### Task 5: Add Missing seccompProfile to Cloudflared

**Files:**
- Modify: `external-lb/k8s/cloudflared-daemonset.yaml`

Cloudflared is missing `seccompProfile: RuntimeDefault` at both pod and container level.

- [ ] **Step 1: Add seccompProfile to pod security context**

Add `seccompProfile: { type: RuntimeDefault }` to the pod-level securityContext.

- [ ] **Step 2: Add container security context**

Add `allowPrivilegeEscalation: false`, `seccompProfile: { type: RuntimeDefault }`, and drop all capabilities to container-level securityContext.

- [ ] **Step 3: Commit**

```bash
git add external-lb/k8s/cloudflared-daemonset.yaml
git commit -m "fix: add seccompProfile and security hardening to cloudflared"
```

---

### Task 6: Fix Branch Protection Check Name Mismatch

**Files:**
- Modify: `scripts/setup-branch-protection.sh`

The script requires "Validate Kustomize Builds" but CI has "Render & Validate Manifests".

- [ ] **Step 1:** Replace `"Validate Kustomize Builds"` with `"Render & Validate Manifests"` in the script.

- [ ] **Step 2: Commit**

```bash
git add scripts/setup-branch-protection.sh
git commit -m "fix: correct branch protection check name to match CI job"
```

---

### Task 7: Add Resource Quotas for Unguarded Namespaces

**Files:**
- Create: `k8s/infra/security/resource-quotas/monitoring-quota.yaml`
- Create: `k8s/infra/security/resource-quotas/minio-quota.yaml`
- Create: `k8s/infra/security/resource-quotas/velero-quota.yaml`

The monitoring, minio, and velero namespaces lack ResourceQuotas.

- [ ] **Step 1:** Create monitoring-quota.yaml (requests: 4 CPU/8Gi RAM, limits: 8 CPU/16Gi RAM, pods: 50)
- [ ] **Step 2:** Create minio-quota.yaml (requests: 2 CPU/4Gi RAM, limits: 4 CPU/8Gi RAM, pods: 10)
- [ ] **Step 3:** Create velero-quota.yaml (requests: 1 CPU/2Gi RAM, limits: 2 CPU/4Gi RAM, pods: 10)
- [ ] **Step 4:** Add quota files to security kustomization.yaml
- [ ] **Step 5: Commit**

```bash
git add k8s/infra/security/
git commit -m "feat: add resource quotas for monitoring, minio, and velero namespaces"
```

---

### Task 8: Remove Replace=true from invenio-bootstrap App

**Files:**
- Modify: `argocd/apps/invenio-bootstrap.yaml`

The `Replace=true` sync option forces delete-and-recreate on every sync, causing unnecessary pod churn. Only the setup Job needs Replace.

- [ ] **Step 1:** Remove `Replace=true` from the app-level syncOptions (keep CreateNamespace and PrunePropagationPolicy)
- [ ] **Step 2: Commit**

```bash
git add argocd/apps/invenio-bootstrap.yaml
git commit -m "fix: remove Replace=true from invenio-bootstrap app to avoid unnecessary pod churn"
```

---

### Task 9: Clean Up Orphaned Files

**Files:**
- Delete: `k8s/apps/invenio-deps/redis/values.yaml` (unused Bitnami Helm values)
- Delete: `k8s/infra/argocd-image-updater/config.yaml` (orphaned ConfigMap)
- Delete: `docker/invenio/build.sh` (stale template with placeholders)
- Delete: `k8s/infra/argocd/install.yaml` (stale local copy — kustomization uses remote URL)

- [ ] **Step 1:** Delete all four orphaned files
- [ ] **Step 2:** Verify kustomization references still work (no missing file errors)
- [ ] **Step 3: Commit**

```bash
git commit -m "chore: remove orphaned config files (redis values, image updater config, stale build.sh, argocd install.yaml)"
```

---

### Task 10: Remove Duplicate AppProject Definition

**Files:**
- Delete: `argocd/projects/invenio-project.yaml` (duplicate of `k8s/infra/argocd/invenio-project.yaml`)

- [ ] **Step 1:** Verify the files are identical (`diff`)
- [ ] **Step 2:** Check if `argocd/projects/` is referenced by root app or bootstrap script
- [ ] **Step 3:** Remove the duplicate if not referenced
- [ ] **Step 4: Commit**

```bash
git commit -m "chore: remove duplicate invenio AppProject (k8s/infra/argocd/ is the canonical copy)"
```

---

### Task 11: Add Prometheus Scrape Ports to Network Policy

**Files:**
- Modify: `k8s/infra/security/network-policies/monitoring-allow.yaml`

Prometheus can't scrape Invenio (5000), OpenSearch (9200), or CNPG (8000) because the egress policy only allows 9090, 9093, 9100, 8080, 8081.

- [ ] **Step 1:** Add ports 5000, 9200, 8000, and 6379 to the `allow-prometheus-egress` NetworkPolicy
- [ ] **Step 2: Commit**

```bash
git commit -m "fix: add missing scrape ports to Prometheus egress network policy"
```

---

## Part C: Medium-Priority Improvements (Hygiene & Consistency)

### Task 12: Standardize ArgoCD Sync Options

**Files:**
- Modify: `argocd/apps/traefik.yaml`
- Modify: `argocd/apps/minio.yaml`
- Modify: `argocd/apps/minio-extras.yaml`
- Modify: `argocd/apps/monitoring-extras.yaml`

Several apps are missing `ServerSideApply=true`.

- [ ] **Step 1-4:** Add `ServerSideApply=true` to each app's syncOptions
- [ ] **Step 5: Commit**

```bash
git commit -m "fix: add ServerSideApply=true to all ArgoCD apps for consistent sync behavior"
```

---

### Task 13: Remove Unused invenio-theme-config ConfigMap

**Files:**
- Potentially delete: `k8s/apps/invenio/invenio-theme-config.yaml`

- [ ] **Step 1:** Verify the ConfigMap is not referenced by any volume mount
- [ ] **Step 2:** Remove from kustomization.yaml and delete file if unused
- [ ] **Step 3: Commit**

```bash
git commit -m "chore: remove unused invenio-theme-config ConfigMap"
```

---

### Task 14: Fix Dockerfile pip Cache Inconsistency

**Files:**
- Modify: `docker/invenio/Dockerfile`

`--no-cache-dir` is used alongside `--mount=type=cache`, making the cache mount useless.

- [ ] **Step 1:** Remove `--no-cache-dir` from pip install commands that use cache mounts
- [ ] **Step 2:** Verify Dockerfile syntax
- [ ] **Step 3: Commit**

```bash
git commit -m "fix: remove redundant --no-cache-dir when using pip cache mounts"
```

---

## Execution Order

1. **Tasks 1-4** — Critical fixes, commit together as "cluster health fix" batch
2. **Tasks 5, 8, 11** — High-priority improvements
3. **Tasks 6, 7** — Medium-priority (quotas + CI fix)
4. **Tasks 9, 10, 12-14** — Hygiene/cleanup (can be batched)
