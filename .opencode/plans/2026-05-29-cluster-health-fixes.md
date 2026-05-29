# Cluster Health Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 5 cluster health issues: CNPG operator CrashLoop, Loki cache blocked by LimitRange, invenio-worker stuck rolling update, Velero maintenance jobs failing, and ArgoCD-self OutOfSync label drift.

**Architecture:** Targeted manifest changes across network policies, limit ranges, deployment probes, velero configuration, and ArgoCD app specs. All changes flow through GitOps (ArgoCD auto-sync from `main`).

**Tech Stack:** Kubernetes manifests (YAML), Kustomize, ArgoCD, Helm values

---

## Tier: T3 — Multi-component, cluster-wide, touches security (netpols) and affects 5 services

**GitHub Issue:** Will be created as `[T3] Fix 5 cluster health issues: CNPG, Loki, worker, Velero, ArgoCD-self`

**Affected Services:** database (CNPG), monitoring (Loki), invenio (worker), velero (maintenance jobs), argocd (self-management)

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| CNPG netpol change allows broader egress | Low — 0.0.0.0/0 on port 443 only, already used by invenio namespace | Verified other namespaces use same pattern |
| Worker scale-down causes brief downtime | Medium — 2 running workers will be terminated | Workers are behind Celery broker; tasks re-queue after pods return |
| Loki cache memory reduction may slow queries | Low — 4Gi still sufficient for small cluster | Monitor query latency after change |
| Velero LimitRange may affect future workloads | Low — only Velero maintenance jobs run in that namespace | 256Mi default is generous for maintenance jobs |
| ArgoCD-self ignoreDifferences is cosmetic | Minimal — only hides label drift | Labels are set by ArgoCD itself, not user-managed |

## Rollback Plan

1. `git revert <sha>` to revert all changes
2. ArgoCD auto-syncs the revert within ~30s
3. For CNPG: delete the new netpol manually if needed: `kubectl delete netpol database-allow-apiserver -n database`
4. For worker: `kubectl scale deploy invenio-worker -n invenio --replicas=2` to restore
5. For Loki: no action needed — cache simply won't be created at 4Gi until a manual sync
6. Velero: maintenance jobs will use new defaults; reverting removes the LimitRange
7. ArgoCD: manual sync with `argocd app sync argocd-self` to force label reconciliation

## File Structure

| File | Action | Purpose |
|------|--------|---------|
| `k8s/apps/invenio-deps/postgresql/network-policy.yaml` | Modify | Add egress rule for K8s API server |
| `k8s/infra/security/limit-ranges/default-limits.yaml` | Modify | Add velero LimitRange |
| `k8s/infra/loki/values.yaml` | Modify | Add memcached resource overrides for chunks-cache |
| `k8s/apps/invenio/invenio-worker-deployment.yaml` | Modify | Fix startup probe (add timeoutSeconds) |
| `k8s/infra/velero/kustomization.yaml` | Modify | Include new LimitRange resource |
| `k8s/infra/velero/limit-range.yaml` | Create | Default resource limits for Velero namespace |
| `argocd/apps/argocd-self.yaml` | Modify | Add ignoreDifferences for label drift |

---

### Task 1: Fix CNPG operator CrashLoop — Add K8s API egress network policy

**Files:**
- Modify: `k8s/apps/invenio-deps/postgresql/network-policy.yaml`

**Context:** The CNPG operator pod crashes because it can't reach the Kubernetes API server at 10.43.0.1:443. The `database-allow-egress` policy only allows DNS (port 53) to kube-system and MinIO (port 9000). The operator needs to communicate with the K8s API on port 443.

- [ ] **Step 1: Add egress rule for K8s API server**

Add the following NetworkPolicy to `k8s/apps/invenio-deps/postgresql/network-policy.yaml`, appended after the last `---`:

```yaml
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: database-allow-apiserver
  namespace: database
spec:
  podSelector: {}
  policyTypes:
    - Egress
  egress:
    - to:
        - ipBlock:
            cidr: 0.0.0.0/0
      ports:
        - port: 443
          protocol: TCP
```

This allows all pods in the `database` namespace to reach the K8s API on port 443. This matches the pattern already used in `k8s/infra/security/network-policies/invenio-netpol.yaml` (line 168: `cidr: 0.0.0.0/0` on port 443). The CNPG operator needs this to manage the cluster CRD.

- [ ] **Step 2: Commit**

```bash
git add k8s/apps/invenio-deps/postgresql/network-policy.yaml
git commit -m "fix: add K8s API egress network policy for CNPG operator (#N)"
```

---

### Task 2: Reduce Loki chunks-cache memory to fit within monitoring LimitRange

**Files:**
- Modify: `k8s/infra/loki/values.yaml`

**Context:** The `monitoring` namespace has a LimitRange with `max: memory 4Gi` per container. Loki's Helm chart configures `loki-chunks-cache` with memcached requesting 9830Mi, which exceeds the 4Gi limit. The pod is forbidden from being created. The `chunksCache.allocated_memory: 256Mi` controls Loki's internal allocation, but the memcached container resources are separate and default to 9830Mi. We need to explicitly override them.

- [ ] **Step 1: Add chunks-cache memcached resource overrides to loki values**

In `k8s/infra/loki/values.yaml`, replace the existing `chunksCache` block:

Before (lines 36-37):
```yaml
chunksCache:
  allocated_memory: 256Mi
```

After:
```yaml
chunksCache:
  allocated_memory: 256Mi
  memcached:
    resources:
      requests:
        cpu: 500m
        memory: 2Gi
      limits:
        cpu: 1
        memory: 4Gi
```

This gives the chunks-cache memcached 4Gi max (within the LimitRange cap), which is sufficient for a small cluster. The `resultsCache` already works fine and doesn't need changes.

- [ ] **Step 2: Commit**

```bash
git add k8s/infra/loki/values.yaml
git commit -m "fix: reduce Loki chunks-cache memcached resources to fit monitoring LimitRange (#N)"
```

---

### Task 3: Fix invenio-worker stuck rolling update and startup probe

**Files:**
- Modify: `k8s/apps/invenio/invenio-worker-deployment.yaml`

**Context:** The worker deployment has a stuck rolling update: 2 old pods (ReplicaSet `5cd9df6c59`) running fine + 1 new pod (ReplicaSet `7bf6486f74`) in CrashLoopBackOff. The `startupProbe` runs `celery inspect ping` with `--timeout 30` but the probe itself is timing out at the 1-second level because `timeoutSeconds` is not set (defaults to 1). The livenessProbe has `timeoutSeconds: 45` and readinessProbe has `timeoutSeconds: 20`, but the startupProbe has **no timeoutSeconds**, defaulting to 1 second — way too low for a Celery command.

**Fix:** Add `timeoutSeconds: 30` to the startupProbe (matching the `--timeout 30` in the command itself). After ArgoCD syncs, clear the stuck rollout by scaling to 0 and back up.

- [ ] **Step 1: Add timeoutSeconds to startupProbe in the manifest**

In `k8s/apps/invenio/invenio-worker-deployment.yaml`, add `timeoutSeconds: 30` after `failureThreshold: 12` in the startupProbe block (between lines 98 and 99):

Before (lines 90-99):
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

After:
```yaml
          startupProbe:
            exec:
              command:
                - /bin/sh
                - -c
                - celery -A invenio_app.celery inspect ping -d celery@$(hostname) --timeout 30
            initialDelaySeconds: 30
            periodSeconds: 15
            timeoutSeconds: 30
            failureThreshold: 12
```

- [ ] **Step 2: Commit**

```bash
git add k8s/apps/invenio/invenio-worker-deployment.yaml
git commit -m "fix: add timeoutSeconds to invenio-worker startupProbe (#N)"
```

- [ ] **Step 3: After ArgoCD syncs, clear the stuck rollout**

This is a manual operational step after the manifest syncs:
```bash
kubectl scale deploy invenio-worker -n invenio --replicas=0
# Wait for pods to terminate
kubectl scale deploy invenio-worker -n invenio --replicas=2
```

The manifest still says `replicas: 2`, so ArgoCD won't fight the scale-down since it's temporary. Once scaled back up, pods start with the fixed probe.

---

### Task 4: Fix Velero Kopia maintenance jobs blocked by ResourceQuota

**Files:**
- Create: `k8s/infra/velero/limit-range.yaml`
- Modify: `k8s/infra/velero/kustomization.yaml`

**Context:** Velero's scheduled Kopia repository maintenance jobs fail because the `velero-quota` ResourceQuota requires all containers to specify resource requests/limits, but the maintenance job containers don't specify any. The fix is to add a LimitRange to the `velero` namespace that provides default resource requests/limits for containers without explicit ones.

- [ ] **Step 1: Create the LimitRange**

Create `k8s/infra/velero/limit-range.yaml`:

```yaml
apiVersion: v1
kind: LimitRange
metadata:
  name: velero-default-limits
  namespace: velero
spec:
  limits:
    - default:
        cpu: 250m
        memory: 256Mi
      defaultRequest:
        cpu: 50m
        memory: 64Mi
      max:
        cpu: 1
        memory: 1Gi
      min:
        cpu: 25m
        memory: 32Mi
      type: Container
```

This gives default resources to containers that don't specify them (like the Kopia maintenance jobs), while capping individual containers at 1 CPU / 1Gi — well within the velero-quota (2 CPU / 4Gi total limits).

- [ ] **Step 2: Add the LimitRange to the Velero kustomization**

In `k8s/infra/velero/kustomization.yaml`, add the new resource:

Before:
```yaml
resources:
  - velero-credentials-secret.yaml
  - backup-schedule.yaml
```

After:
```yaml
resources:
  - velero-credentials-secret.yaml
  - backup-schedule.yaml
  - limit-range.yaml
```

- [ ] **Step 3: Commit**

```bash
git add k8s/infra/velero/limit-range.yaml k8s/infra/velero/kustomization.yaml
git commit -m "fix: add LimitRange to velero namespace for Kopia maintenance jobs (#N)"
```

---

### Task 5: Fix ArgoCD-self OutOfSync label drift

**Files:**
- Modify: `argocd/apps/argocd-self.yaml`

**Context:** ArgoCD-self is OutOfSync because it wants to add the `app.kubernetes.io/instance: argocd-self` label to ArgoCD's own Deployments/StatefulSets, but these labels were set by the original upstream `install.yaml` and ArgoCD's `ApplyOutOfSyncOnly` mode can't reconcile them. This causes a persistent OutOfSync status and SyncError. The fix is to add `ignoreDifferences` for this label.

- [ ] **Step 1: Add ignoreDifferences to argocd-self app**

In `argocd/apps/argocd-self.yaml`, add an `ignoreDifferences` block after `syncOptions`:

Before (lines 24-29):
```yaml
  syncPolicy:
    automated:
      prune: false
      selfHeal: true
    syncOptions:
      - PruneLast=true
      - ApplyOutOfSyncOnly=true
      - ServerSideApply=true
```

After:
```yaml
  syncPolicy:
    automated:
      prune: false
      selfHeal: true
    syncOptions:
      - PruneLast=true
      - ApplyOutOfSyncOnly=true
      - ServerSideApply=true
  ignoreDifferences:
    - group: apps
      kind: Deployment
      jqPathExpressions:
        - .metadata.labels["app.kubernetes.io/instance"]
    - group: apps
      kind: StatefulSet
      jqPathExpressions:
        - .metadata.labels["app.kubernetes.io/instance"]
```

This tells ArgoCD to ignore the `app.kubernetes.io/instance` label drift on ArgoCD's own Deployments and StatefulSets, which is set by the upstream install.yaml and can't be overridden by kustomize patches.

- [ ] **Step 2: Commit**

```bash
git add argocd/apps/argocd-self.yaml
git commit -m "fix: ignore ArgoCD-self label drift for instance label (#N)"
```

---

### Task 6: Create GitHub Issue, Branch, and PR

Follow the issue-to-pr-workflow skill:

- [ ] **Step 1: Create the GitHub issue** with `[T3] Fix 5 cluster health issues: CNPG, Loki, worker, Velero, ArgoCD-self` using the full T3 template (Risk Assessment, Rollback Plan, Affected Services)

- [ ] **Step 2: Create the branch** from main: `fix/<issue-number>-cluster-health`

- [ ] **Step 3: Cherry-pick or re-apply all 5 fix commits onto the branch**

- [ ] **Step 4: Push and create the PR** with `Closes #<issue-number>` and the summary of all 5 changes

---

### Post-Merge Verification

After the PR merges and ArgoCD syncs (wait ~30s-2min):

```bash
# 1. Check all ArgoCD apps
kubectl get applications -n argocd

# 2. Verify CNPG operator is running
kubectl get pods -n database

# 3. Verify Loki chunks-cache
kubectl get pods -n monitoring | grep chunks

# 4. Verify worker status
kubectl get pods -n invenio -l app.kubernetes.io/name=invenio-worker

# 5. Verify Velero maintenance jobs (check events for quota errors)
kubectl get events -n velero | grep -i quota

# 6. Clear worker stuck rollout if needed
kubectl scale deploy invenio-worker -n invenio --replicas=0
# Wait for pods to terminate
kubectl scale deploy invenio-worker -n invenio --replicas=2
```