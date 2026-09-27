# KubeMemoryQuotaOvercommit — offline diagnosis + quota fix (2026-09-27)

Branch: `agent/alert-quota` (unmerged). No HPA/deployment/replica changes (owned by alert-hpa worker).
kubectl unreachable from this machine (10.17.104.130 connection refused) — live confirmation is an open gate for the operator.

## 1. Alert semantics (evidence, not guess)

`KubeMemoryQuotaOvercommit` (kube-prometheus-stack 69.6.0, `rules-1.14/kubernetes-resources.yaml`) is
**cluster-scoped, not per-namespace**:

```
sum(min without(resource) (kube_resourcequota{type="hard", resource=~"(memory|requests.memory)"}))
/ sum(kube_node_status_allocatable{resource="memory"})
> 1.5   for 5m, severity warning
```

- No `namespace` label exists on this alert by construction. Discord showing no namespace is
  expected for this alert (the template groups by alertname/namespace/service, but there is no
  namespace label to render). A per-namespace sibling (`KubeQuotaExceeded`, used/hard > 90% for
  15m) *does* carry namespace — if that fires, the Discord template still hides it (message only
  renders summary/description). Discord template fix is out of scope for this change set
  (follow-up candidate, needs operator call).
- Severity `warning` routes to `discord-warning` with `repeatInterval: 12h`
  (`k8s/infra/monitoring/discord-receivers.yaml`). A constantly-firing alert re-notifies every
  12h — this explains the ~6am/6pm recurrence. No CronJob in repo runs at 6am/6pm: Velero weekly
  is Sunday `0 3 * * 0`, CNPG daily backup is `0 2 * * *`. The cadence is a repeat interval,
  not a workload schedule.
- Rollout surge double-count is real for *used-vs-hard* pressure (old+new pods count during
  RollingUpdate; precedent: monitoring 96% CPU quota wedged a rollout, fixed 16→24 in #74), so
  every proposed quota below keeps ≥1.4x worst-pattern headroom including surge.

## 2. Audit: per-namespace requests (manifests) vs quota hard

Method: summed `requests.memory` over app + init containers at stated replicas from manifests in
`k8s/apps`, `k8s/infra` and chart `values.yaml`. Completed Jobs (invenio-setup, pipecheck) are
terminal and count 0 toward quota used. DaemonSets counted at 3 nodes except loki-canary (2 live
pods per #74 notes). argocd-image-updater upstream resources unknown — estimated 128Mi, flagged
for live check. CNPG postgres has no `resources:` stanza — LimitRange-filled at 128Mi/container,
assumed ≤2 containers (flagged for live check). OpenSearch init without resources filled at
128Mi by search LimitRange.

| Rank | Namespace | Steady req (manifests) | Quota hard (before) | Util | Max / surge pattern |
|------|-----------|------------------------|---------------------|------|---------------------|
| 1 | argocd | ~1120Mi (992 + updater est) | 4Gi | ~27% | staggered-surge ~1.7Gi |
| 2 | monitoring | 2752Mi (prom-stack + loki) | 12Gi | 22% | surge ~3.25Gi |
| 3 | invenio | 2176Mi (web2 + worker1 + sched) | 12Gi | 18% | HPA-max 2.91Gi / surge 4.5Gi + setup 0.5Gi |
| 4 | search | 640Mi (512 + 128 init-fill) | 4Gi | 16% | no surge (single replica) |
| 4 | velero | ~320Mi (128 + node-agent 3×64) | 2Gi | 16% | backup spikes inside node-agent |
| 6 | redis | 128Mi | 2Gi | 6% | no surge (maxSurge 0) |
| 6 | minio | 256Mi (observed ~250Mi) | 4Gi | 6% | surge 512Mi |
| 8 | database | ~128–256Mi (LimitRange-filled) | 8Gi | ~3% | no surge pattern |
| 9 | default | 0 (empty) | 512Mi | 0% | — |
| — | traefik | 128Mi (2×64) | none | n/a | no quota; node pressure only |
| — | ouroboros | — | — | — | **no manifests found in repo; nothing to audit** |

**No namespace is near its individual quota (max 27%).** Per brief instruction, no firing
namespace is guessed: the firing condition is the **aggregate**.

Aggregate before: 12+12+4+8+2+4+4+2+0.5 = **48.5Gi** hard requests.memory.
Node allocatable: 7918Mi/worker × 3 nodes = **23.2Gi** → ratio **2.09 > 1.5 → fires constantly**.
(Effective schedulable is 2 workers ≈ 15.5Gi since control-plane is CriticalAddonsOnly-tainted;
ratio vs schedulable is 3.14 — the alert denominator still includes the control-plane, but true
scheduling headroom is tighter. Noted, not acted on here.)

## 3. Decision log

1. Fix the aggregate, not a single namespace: lower `requests.memory` hards to a 27.5Gi total
   (ratio 1.19, 7.3Gi margin under the 34.8Gi = 1.5× budget). Only `requests.memory` edited —
   CPU, limits, pods, and all LimitRanges untouched (alert regex only consumes requests.memory;
   limits cuts are a separate capacity discussion).
2. invenio set to 7Gi (not 6Gi): 5.0Gi worst pattern × 1.4 = 7Gi, and it reserves HPA 2→3 growth
   (worst ≈ 5.9Gi, 1.1Gi slack). HPA max is owned by the alert-hpa worker — see open question.
3. velero + default untouched: velero kept at 2Gi for backup data-mover spikes (DR safety, Sunday
   schedule); default already minimal on an empty namespace.
4. Discord template (`discord-receivers.yaml`) NOT touched: outside the allowed file list for this
   change set. Namespace visibility for per-namespace alerts is a follow-up proposal.
5. No HPA/deployment/replica edits (explicitly forbidden — alert-hpa worker owns them).

## 4. Proposed/applied diff (all `requests.memory` hard only)

| File | Before → After | Rationale (headroom over worst pattern) |
|------|---------------|------------------------------------------|
| `k8s/apps/invenio/namespace-governance.yaml` | 12Gi → **7Gi** | 5.0Gi worst × 1.4; 2.4x HPA-max; −5Gi |
| `k8s/infra/security/resource-quotas/monitoring-quota.yaml` | 12Gi → **6Gi** | 3.25Gi surge × 1.85; −6Gi |
| `k8s/apps/invenio-deps/postgresql/namespace.yaml` | 8Gi → **4Gi** | 3-instance HA + jobs fit with 5x; −4Gi |
| `k8s/apps/invenio-deps/opensearch/manifests/namespace.yaml` | 4Gi → **2Gi** | 0.64Gi × 3.2; −2Gi |
| `k8s/apps/invenio-deps/redis/manifests/namespace.yaml` | 2Gi → **1Gi** | 0.125Gi × 8; −1Gi |
| `k8s/infra/security/resource-quotas/minio-quota.yaml` | 4Gi → **2Gi** | 0.5Gi surge × 4; −2Gi |
| `k8s/infra/security/resource-quotas/argocd-quota.yaml` | 4Gi → **3Gi** | 1.7Gi staggered-surge × 1.76; −1Gi |
| `k8s/infra/security/resource-quotas/velero-quota.yaml` | 2Gi (keep) | DR safety |
| `k8s/infra/security/resource-quotas/default-quota.yaml` | 512Mi (keep) | already minimal |

New aggregate: 7+6+4+2+1+2+3+2+0.5 = **27.5Gi** → 27.5/23.2 = **1.19 < 1.5**. Clears the alert
with 7.3Gi margin.

## 5. Verification (offline, this machine)

- `kustomize build` exits 0 for all 5 touched kustomizations: `k8s/infra/security`,
  `k8s/apps/invenio`, `k8s/apps/invenio-deps/postgresql`,
  `k8s/apps/invenio-deps/opensearch/manifests`, `k8s/apps/invenio-deps/redis/manifests`.
  Rendered quotas confirmed: 7Gi / 6Gi / 4Gi / 2Gi / 1Gi / 3Gi / 2Gi / 512Mi.
- `yamllint` clean on all touched quota files.
- `kubectl cluster-info` fails with connection refused (10.17.104.130) as expected — no live
  verification possible from here; operator gates below are OPEN.

## 6. Open live gates (operator, with cluster access)

```promql
# Should drop below 1.5 after ArgoCD syncs the new quotas:
sum(min without(resource) (kube_resourcequota{type="hard", resource=~"(memory|requests.memory)"}))
/ sum(kube_node_status_allocatable{resource="memory"})
# Per-namespace fill (expect all < 50% steady):
100 * kube_resourcequota{type="used", resource="requests.memory"}
  / ignoring(instance,job,type) kube_resourcequota{type="hard", resource="requests.memory"}
```

```bash
kubectl describe resourcequota -A | grep -B2 -A6 "requests.memory"
kubectl -n argocd get deploy argocd-image-updater -o jsonpath='{.spec.template.spec.containers[*].resources}'
kubectl -n database get pod -l cnpg.io/cluster=postgres -o jsonpath='{..resources}'
kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.allocatable.memory}{"\n"}{end}'
```

Watch one ArgoCD sync wave + one invenio rollout (surge fits 7Gi) and the next two 12h Discord
windows for the alert going silent.

## 7. Rollback

Revert this branch's quota commits (ArgoCD self-heals; raising quotas never blocks running pods).
Safe direction note: if any namespace's *used* ever approaches the new hard during a surge,
raise that namespace first, then re-check the aggregate stays < 34.8Gi.

## 8. Open questions (STOP — need operator / alert-hpa worker, not guessed)

1. **HPA growth**: if alert-hpa raises invenio-worker maxReplicas 2→3 (worker CPU saturation is a
   known separate issue), worst-case surge+setup ≈ 5.9Gi still fits 7Gi (1.1Gi slack). Confirm
   with the alert-hpa worker; do NOT lower invenio below 7Gi without their sign-off.
2. **argocd-image-updater + CNPG actual requests**: live values needed (estimated above); if the
   updater requests more than 128Mi or CNPG runs 3+ containers, re-check argocd/database headroom.
3. **Discord namespace visibility**: propose (separate change) rendering `.Labels.namespace` in
   discord messages so the per-namespace `KubeQuotaExceeded` alert identifies its namespace.
   This cluster-level alert will still carry no namespace by design.
4. **ouroboros**: named in the brief but absent from the repo — confirm whether it exists
   in-cluster and, if so, which manifests govern it.
