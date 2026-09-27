# Wave Report: alert triage (issue #131) — HPA + quota workers

Integrated by lead from `agent/alert-hpa-metrics` and `agent/alert-quota`.
See `docs/plans/active/2026-09-27-hpa-maxed-out.md` and `docs/plans/active/2026-09-27-quota-overcommit.md`.

## Lead reconciliation (2026-09-27)
- Conflict: HPA worker staged max 4/4; quota worker sized invenio 7Gi for
  max 2+2 with 2->3 growth. Worst-pattern at 4/4 + rollout surge + setup
  Job ~= 7.4Gi > 7Gi -> potential FailedCreate wedge at peak.
- Decision: invenio quota 7Gi -> 8Gi (aggregate 28.5Gi = 1.23x node
  allocatable, still clears the >1.5x alert threshold). Worker reports
  below are preserved verbatim as historical record.
- Open live gates (VPN needed): `describe hpa` pinning metric, apiservice
  health, `describe resourcequota` fill, two 6am/6pm soak windows.

---
# PART A — alert-hpa
# WORKER-REPORT: KubeHpaMaxedOut (web + worker) + KubeAggregatedAPIDown (v1beta1.metrics.k8s.io)

Branch: `agent/alert-hpa-metrics` · Date: 2026-09-27 · Scope: diagnosis + fix-ready changeset, UNMERGED (see §8; live gates open)
Method: systematic-debugging Phases 1–4 (manifest evidence; no cluster access — `kubectl` returns `connection refused`, see §5/§8)

## 1. Root-cause hypothesis

**H1 (primary, HPA maxed): capacity ceiling + hair-trigger thresholds + static replica conflict.**
Both HPAs have `min: 1 / max: 2` with CPU 70% + memory 80% on small requests, and `invenio-web` Deployment pins static `replicas: 2` (= max already). Any sustained load over ~175m CPU / ~410Mi mem (web) or ~350m CPU / ~614Mi mem (worker) pins replicas at max for the 15m `for:` window. The 6am/6pm recurrence points to real periodic load (celery beat fan-out via `invenio-scheduler`, and/or external traffic/cron), not the already-fixed probe burn. Memory-targeted HPA makes this sticky: memory does not drop linearly when adding pods, so a memory-driven scale-up wedges at max.

**H2 (metrics API 80%): RKE2 metrics-server flapping, not repo-managed.**
No `metrics-server` / `APIService` manifest exists in this repo (verified by grep: only `kustomize.config.k8s.io/v1beta1` hits). RKE2 ships metrics-server as a kube-system addon outside ArgoCD. ~80% availability over 10m ≈ ~2 min down: typical causes are single-replica eviction/OOM, node pressure, cert rotation, or overload during the same 6am/6pm peaks. No NetworkPolicy in repo targets kube-system, so repo netpols are unlikely blockers — but live `kubectl get apiservice` is required to confirm.

**H3 (interaction, must disambiguate live): metrics gap ⇄ HPA max.**
If metrics-server is down, HPA gets `FailedGetResourceMetric` / `Unknown` metrics and holds replicas at max (fires KubeHpaMaxedOut without real load). Conversely, real 6am/6pm load can overload metrics-server. `kubectl describe hpa` conditions (`ScalingLimited`, `AbleToScale`, metric values vs targets) plus `apiservice` availability timestamps decide the direction. Do not scale blindly until this is answered.

Prior fix context: `46720d4` (probe burn #127) cut worker idle 677/838m → 1m/1m and HPA 186% → 35%, but the same close-out plan explicitly flagged residual `invenio-web-hpa` memory 77%/80% at 2/2 — i.e. web was already on the edge of max with no headroom.

## 2. Evidence table

| # | Fact (file:line) | Meaning |
|---|---|---|
| E1 | `k8s/apps/invenio/invenio-hpa.yaml:13-14,41-42` — min 1 / max 2 both HPAs | Ceiling of 2: one extra pod of headroom total; any peak pins at max |
| E2 | `invenio-hpa.yaml:15-27,43-55` — CPU 70% + memory 80%, no `behavior:` block | Dual-metric, no stabilization windows: aggressive/default scale-up, flappy scale-down; memory metric wedges at max |
| E3 | `invenio-deployment.yaml:14` — `replicas: 2` alongside HPA min 1/max 2 | Web starts AT max; HPA can never scale up, only sit maxed. Standard practice is to remove `replicas` when HPA manages it. Worker file has no `replicas:` field (defaults to 1) — inconsistent |
| E4 | `invenio-deployment.yaml:119-125` — web req 250m/512Mi, lim 1000m/2Gi | HPA targets = 175m CPU / ~410Mi mem. Tiny absolute headroom per pod |
| E5 | `invenio-worker-deployment.yaml:89-94` — worker req 500m/768Mi, lim 1000m/2Gi | HPA targets = 350m CPU / ~614Mi mem. Post-#127 idle is 1m/35% (plan §lead-proof), so 6am/6pm max must be real task load, not probes |
| E6 | `invenio-worker-deployment.yaml:95-130` — stretched probes (300/25, 120/25) post-`46720d4` | Probe burn fixed and verified live; recurrence is NOT probe regression unless image/entrypoint reverted — check live pod spec |
| E7 | `invenio-scheduler-deployment.yaml:14` — singleton `replicas: 1`, celery beat | Only in-repo periodic driver: beat schedule inside image can fan out 6am/6pm jobs to worker + pressure web (reindex/stats). No CronJob kind in invenio namespace (grep: only infra-project allows CronJob) |
| E8 | `docs/plans/completed/2026-09-15-worker-probe-burn.md:36-37,44-53` — web mem 77%/80% residual; worker 186%→35% | Web maxed-alert was predicted leftover; current recurrence matches that warning, not a new failure mode |
| E9 | `k8s/infra/monitoring/alerts.yaml` — no KubeHpaMaxedOut / KubeAggregatedAPIDown rules | Both alerts are upstream kube-prometheus-stack defaults (not repo-tunable except via chart `defaultRules`). Repo change = fix capacity/health, not silence rules |
| E10 | grep `metrics-server\|metrics.k8s.io\|APIService` in `k8s/` — zero app manifests | Metrics API is RKE2 addon (kube-system), outside GitOps. Fix is live triage, not a repo diff |
| E11 | `k8s/infra/security/network-policies/` — default-deny covers argocd/default/monitoring/minio/velero/traefik, invenio has own deny+allows; nothing selects kube-system | Repo netpols do not obviously block metrics-server :4443; live check still needed (host-level or RKE2 netpol outside repo possible) |
| E12 | `k8s/apps/invenio/namespace-governance.yaml` — quota req 6 CPU/12Gi, lim 16/32Gi, pods 30 | Headroom for scale-up exists on paper but NOT verified live; quota/limitrange edits explicitly out of scope (other worker) — do not raise HPA max past quota without that worker's sign-off |
| E13 | `k8s/apps/invenio/invenio-pdb.yaml:9,22` — `minAvailable: 1` both | Safe for current 2-max; any scale-up keeps PDB satisfied; scale-down to 1 still protected |
| E14 | `kubectl cluster-info` from this machine 2026-09-27 | `dial tcp 10.17.104.130:443: connect: connection refused` — no cluster access; all conclusions are manifest + history based, pending live proof in §5 |

Threshold math (for §3 sizing): web 70%×250m=175m, 80%×512Mi≈410Mi; worker 70%×500m=350m, 80%×768Mi≈615Mi.

## 3. Proposed diff (NOT applied — pending live verification in §5)

Rationale: give peaks headroom, stop HPA fighting static replicas, damp flapping, and make memory non-wedging. Sizes are starting points; confirm against live `kubectl top` + quota usage before merging.

```diff
--- a/k8s/apps/invenio/invenio-hpa.yaml
+++ b/k8s/apps/invenio/invenio-hpa.yaml
@@ web
-  minReplicas: 1
-  maxReplicas: 2
+  minReplicas: 2
+  maxReplicas: 4
@@ worker
-  minReplicas: 1
-  maxReplicas: 2
+  minReplicas: 1
+  maxReplicas: 4
@@ both HPAs (add)
+  behavior:
+    scaleUp:
+      stabilizationWindowSeconds: 60
+      policies:
+        - type: Percent
+          value: 100
+          periodSeconds: 60
+    scaleDown:
+      stabilizationWindowSeconds: 300
+      policies:
+        - type: Percent
+          value: 50
+          periodSeconds: 120
 # Option B (if live shows memory-wedged, CPU-fine): drop the memory
 # metric and run CPU-only, or raise memory target 80 -> 90.
 # Decision deferred to Q1/Q2 in §6.

--- a/k8s/apps/invenio/invenio-deployment.yaml
+++ b/k8s/apps/invenio/invenio-deployment.yaml
 spec:
-  replicas: 2
+  # replicas removed: HPA owns replica count (min/max in invenio-hpa.yaml).
+  # Keeping a static value fights the HPA and masks ScalingLimited.
```

Explicitly NOT proposed here: quota/limitrange changes (other worker owns them); metrics-server manifests (RKE2 addon, live-only); silencing upstream `KubeHpaMaxedOut` / `KubeAggregatedAPIDown` rules (would hide the signal).

## 4. Live verification — kubectl (operator, needs VPN/cluster access)

```bash
# 0. Baseline: are we still maxed, and why?
kubectl -n invenio get hpa invenio-web-hpa invenio-worker-hpa -o wide
kubectl -n invenio describe hpa invenio-web-hpa invenio-worker-hpa  # check Conditions: ScalingLimited / AbleToScale / FailedGetResourceMetric, current vs target per metric

# 1. Real load or metrics gap? (run during/just after a 6am/6pm firing)
kubectl -n invenio top pods --sort-by=cpu
kubectl -n invenio top pods --sort-by=memory
kubectl -n invenio get pods -o wide  # are there really 2/2 (web) + 2/2 (worker)? any restarts?

# 2. Metrics API health (the 80% alert)
kubectl get apiservice v1beta1.metrics.k8s.io -o yaml  # AVAILABLE True? last transition? response time?
kubectl -n kube-system get pods -l k8s-app=metrics-server -o wide
kubectl -n kube-system top pods -l k8s-app=metrics-server 2>&1 | head
kubectl -n kube-system logs -l k8s-app=metrics-server --tail=100  # OOM? cert? timeouts? leader?
kubectl get events -n kube-system --sort-by=.lastTimestamp | tail -30

# 3. What runs at 6am/6pm?
kubectl -n invenio logs deploy/invenio-scheduler --tail=200 --since=3h  # beat schedule / task fan-out near alert time
kubectl -n invenio logs deploy/invenio-worker --tail=100 --since=3h | grep -iE 'task|received|succeeded|failed|memory|oom' | tail -40
kubectl -n velero get schedules -A 2>/dev/null; kubectl -n cnpg-system get scheduledbackups -A 2>/dev/null || true  # backup overlap?
kubectl get events -n invenio --sort-by=.lastTimestamp | tail -30

# 4. Quota headroom before raising max (coordinate with quota worker)
kubectl -n invenio describe resourcequota invenio-quota
kubectl -n invenio describe limitrange invenio-limits

# 5. Post-change acceptance (after applying §3 on a maintenance window)
kubectl -n invenio get hpa -w  # expect < max outside peaks; scale-up during next 6am/6pm without hitting new max
kubectl get apiservice v1beta1.metrics.k8s.io -o jsonpath='{.status.conditions[?(@.type=="Available")].status}{"\n"}'  # expect True stable 10m+
```

## 5b. Live verification — PromQL (Grafana Explore, `monitoring` Prometheus)

```promql
# HPA state: desired vs max (1.0 = pinned at max)
kube_hpa_spec_max_replicas{namespace="invenio"} / clamp_min(kube_hpa_status_desired_replicas{namespace="invenio"},1)
kube_hpa_status_current_replicas{namespace="invenio"} / kube_hpa_spec_max_replicas{namespace="invenio"}

# Which metric is driving? (compare current/target per HPA)
kube_hpa_status_target_metric{namespace="invenio"}  # check metric_name cpu|memory, desired vs current utilization

# Real usage vs HPA thresholds (web: cpu 0.175, mem ~429M; worker: cpu 0.350, mem ~644M)
sum(rate(container_cpu_usage_seconds_total{namespace="invenio",pod=~"invenio-web-.*"}[5m])) by (pod)
  / on(pod) kube_pod_container_resource_requests{namespace="invenio",resource="cpu",pod=~"invenio-web-.*"}
sum(container_memory_working_set_bytes{namespace="invenio",pod=~"invenio-web-.*"} ) by (pod)
  / on(pod) kube_pod_container_resource_requests{namespace="invenio",resource="memory",pod=~"invenio-web-.*"}
# repeat with pod=~"invenio-worker-.*"

# Metrics API availability (the 80% alert = aggregated-apiserver availability SLI)
avg_over_time(probe_success{job=~".*apiserver.*|.*metrics.*"}[10m])  # adapt to local job labels; fallback:
up{job=~".*metrics-server.*"}  # expect 1 steady; flaps correlate with HPA Unknown windows
count_over_time(kube_apiservice_status_available{api_group="metrics.k8s.io"}[10m])  # if exported

# 6am/6pm pattern: 7-day heatmap of HPA desired + worker CPU
max_over_time(kube_hpa_status_desired_replicas{namespace="invenio"}[15m])
sum(rate(container_cpu_usage_seconds_total{namespace="invenio",pod=~"invenio-worker-.*"}[15m]))
```

Acceptance: outside peaks both HPAs sit below max with per-metric utilization under target; next two 6am/6pm windows scale without pinning 15m; `v1beta1.metrics.k8s.io` Available=True steady (no 80% dips); ArgoCD Synced+Healthy; Deploy Verify green.

## 6. Escalation questions (ambiguous — recorded, not guessed)

- Q1: After live `describe hpa`, which metric pins each HPA (CPU or memory)? If memory-wedged but CPU-fine, do we drop the memory metric / raise it to 90%, or keep dual-metric? Recommend CPU-primary; need operator call.
- Q2: Approved new maxReplicas? Proposal is 4/4 (web min 2). Quota worker must confirm `requests.cpu: 6` / `pods: 30` headroom including deps (postgres/redis/opensearch) before merge.
- Q3: Is the 6am/6pm driver an invenio beat job (tunable schedule/concurrency), a backup window (Velero/CNPG overlap), or external traffic (Cloudflare/Traefik 6am/6pm spike)? Live scheduler + backup + Traefik logs decide; fix differs per answer.
- Q4: metrics-server remediation path: RKE2 addon values are outside this repo — does the operator patch RKE2 config (replicas/resources) out-of-band, or do we vendor an override? Plus: acceptable to add a repo `PrometheusRule` runbook annotation for `KubeAggregatedAPIDown`, or keep upstream rules untouched?
- Q5: Web `replicas: 2` removal — confirm no ArgoCD diff-fighting concern (HPA-owned field) before applying; standard `ignoreDifferences` not currently set for replicas.

## 7. Rollback notes

- This report applied zero manifest changes, so nothing to roll back from this branch.
- When §3 is later applied: revert is `git revert <merge-SHA>` (HPA + deployment replicas) followed by ArgoCD sync; HPA reverts are hitless (replica count re-converges within 1–2 sync/scale cycles). If scale-up overshoots quota, `kubectl -n invenio scale deploy/invenio-web --replicas=2` as immediate manual clamp, then revert the PR.
- metrics-server live actions (restart/resize) are out-of-band: record the exact command + pod state before/after; restart is safe (HPA holds last count on missing metrics, recovers on return).
- Risk if §3 applied without §5 answers: raising max without quota sign-off can wedge scheduling (Pending pods); dropping memory metric without proof can mask a real memory leak → OOMKills. Hence gate on live data.

## 8. Fix-ready changeset (applied 2026-09-27, UNMERGED — live gates open)

### What changed (exact diff)

`k8s/apps/invenio/invenio-hpa.yaml`:
- `invenio-web-hpa`: `minReplicas` 1→2, `maxReplicas` 2→4, added `behavior:` (scaleUp stabilization 60s / 100% per 60s; scaleDown stabilization 300s / 50% per 120s).
- `invenio-worker-hpa`: `minReplicas` 1 (unchanged), `maxReplicas` 2→4, added identical `behavior:` block.
- Metrics untouched: CPU 70% + memory 80% kept (driver ambiguous — Q1 open, so no metric dropped).

`k8s/apps/invenio/invenio-deployment.yaml`:
- Removed static `replicas: 2`; HPA now owns the replica count (comment left in place explaining why).

Deliberately NOT touched: `namespace-governance.yaml` (quota/limitrange — other worker owns; read-only check only), any metrics-server manifest (none created — RKE2 addon, live-only), `alerts.yaml` / upstream kube-prometheus rules (no silencing).

```diff
# invenio-hpa.yaml (both HPAs; web min 1->2, both max 2->4, +behavior)
+  behavior:
+    scaleUp:
+      stabilizationWindowSeconds: 60
+      policies:
+        - type: Percent
+          value: 100
+          periodSeconds: 60
+    scaleDown:
+      stabilizationWindowSeconds: 300
+      policies:
+        - type: Percent
+          value: 50
+          periodSeconds: 120
# invenio-deployment.yaml
-  replicas: 2
+  # replicas intentionally omitted: invenio-web-hpa owns the replica count
```

### Verification commands and output (this machine, 2026-09-27)

- `kustomize build k8s/apps/invenio > /tmp/invenio-build.yaml` → `BUILD_OK`; rendered output contains 2 `HorizontalPodAutoscaler` objects, both with `behavior:` blocks (verified via grep on built YAML).
- `yamllint k8s/apps/invenio/invenio-hpa.yaml k8s/apps/invenio/invenio-deployment.yaml` → `YAMLLINT_CLEAN` (exit 0, no findings).
- `kubectl get hpa -n invenio` and `kubectl get apiservice v1beta1.metrics.k8s.io` (read-only attempts) → `dial tcp 10.17.104.130:443: connect: connection refused` (fresh 22:08 UTC). Cluster unreachable as expected; live gates below remain OPEN.
- Quota math (static, read-only): worst-case new app usage = web 4×(250m/1000m) + worker 4×(500m/1000m) + scheduler (100m/500m) + setup-job transient (250m/1000m) ≈ 3.35 CPU req / 9.5 CPU lim vs quota req 6 / lim 16. Fits ON PAPER, but live `describe resourcequota` usage (including any other invenio-namespace consumers) is unknown — Q2 stays open, quota worker sign-off required before merge.

### What remains — open live gates (from §5, all unrun against the cluster)

- §5.0: `describe hpa` both — which metric pins, any `FailedGetResourceMetric` during metrics dips.
- §5.1: `top pods` web/worker at peak vs threshold math (web 175m/410Mi, worker 350m/615Mi).
- §5.2: `get apiservice v1beta1.metrics.k8s.io -o yaml` + metrics-server pods/logs/events (KubeAggregatedAPIDown triage — live-only, no repo change).
- §5.3: 6am/6pm driver — scheduler/worker logs, Velero/CNPG overlap, Traefik traffic.
- §5.4: `describe resourcequota/limitrange` — headroom for max 4.
- §5.5 (post-merge): two 6am/6pm windows without 15m pin; metrics API Available=True steady; ArgoCD Synced+Healthy; Deploy Verify green.

### Answers needed for Q1–Q5 (all OPEN — recorded, not guessed)

- Q1 (memory-vs-CPU driver): ambiguous from manifests alone. DECISION: kept dual-metric; did NOT drop/raise the memory target. Operator must answer from live `describe hpa` + PromQL (`kube_hpa_status_target_metric`) before any metric tuning.
- Q2 (quota headroom): static math fits but live usage unknown. DECISION: max 4 staged but merge gated on quota worker's `describe resourcequota` sign-off.
- Q3 (6am/6pm driver): beat vs backup vs traffic undetermined without logs. DECISION: no schedule/concurrency changes made; HPA headroom absorbs any of the three.
- Q4 (metrics-server path): RKE2 out-of-band patch vs vendored override undecided; no runbook annotation added (upstream rules untouched). Operator call.
- Q5 (replicas-field removal vs ArgoCD): removal applied per §3; first post-merge sync must be watched for diff-fighting (no `ignoreDifferences` for replicas exists). If ArgoCD flaps, follow-up is an `ignoreDifferences` stanza — not added pre-emptively.

Rollback: `git revert` this change set + ArgoCD sync (hitless, 1–2 scale cycles); emergency clamp `kubectl -n invenio scale deploy/invenio-web --replicas=2`. Never merge to main from this worker.

---
# PART B — alert-quota
# WORKER-REPORT — alert-quota (KubeMemoryQuotaOvercommit)

Branch: `agent/alert-quota`. **Not merged to main** (per constraints). No secrets touched.
No HPA/deployment/replica edits (owned by alert-hpa worker).

## What changed

Lowered `requests.memory` ResourceQuota hards (only that field; CPU/limits/LimitRanges untouched)
so the cluster-aggregate quota sum clears the `KubeMemoryQuotaOvercommit` >1.5x threshold:

- `k8s/apps/invenio/namespace-governance.yaml`: 12Gi → 7Gi
- `k8s/infra/security/resource-quotas/monitoring-quota.yaml`: 12Gi → 6Gi
- `k8s/apps/invenio-deps/postgresql/namespace.yaml`: 8Gi → 4Gi
- `k8s/apps/invenio-deps/opensearch/manifests/namespace.yaml`: 4Gi → 2Gi
- `k8s/apps/invenio-deps/redis/manifests/namespace.yaml`: 2Gi → 1Gi
- `k8s/infra/security/resource-quotas/minio-quota.yaml`: 4Gi → 2Gi
- `k8s/infra/security/resource-quotas/argocd-quota.yaml`: 4Gi → 3Gi
- velero (2Gi) + default (512Mi): kept by design (DR safety / already minimal)

Docs: created `docs/plans/active/2026-09-27-quota-overcommit.md` (audit table, decision log,
PromQL + kubectl gates, rollback, open questions).

Root cause (evidence): the alert is cluster-aggregate
`sum(hard requests.memory) / sum(node allocatable) > 1.5`, not per-namespace usage.
Before: 48.5Gi / 23.2Gi (3×7918Mi) = 2.09 → constantly firing. After: 27.5Gi / 23.2Gi = 1.19.
Per-namespace audit ranked all namespaces ≤27% of their own quota — no single firing namespace
exists, so none was guessed. The ~6am/6pm cadence is the warning-tier `repeatInterval: 12h`,
not a CronJob (Velero Sun 3am, CNPG daily 2am).

## Verification (this machine)

- `kustomize build` exit 0: `k8s/infra/security`, `k8s/apps/invenio`,
  `k8s/apps/invenio-deps/postgresql`, `k8s/apps/invenio-deps/opensearch/manifests`,
  `k8s/apps/invenio-deps/redis/manifests`. Rendered `requests.memory` confirmed:
  7Gi / 6Gi / 4Gi / 2Gi / 1Gi / 3Gi / 2Gi / 512Mi.
- `yamllint` clean on all touched quota files (exit 0).
- `kubectl cluster-info`: connection refused (10.17.104.130) — expected, no cluster access.

## What remains (open live gates for operator)

1. Sync + check aggregate ratio PromQL < 1.5 (query in plan doc §6).
2. `kubectl describe resourcequota -A` per-namespace fill check (expect <50% steady).
3. Confirm live `argocd-image-updater` (est. 128Mi) and CNPG container requests (est. ≤256Mi).
4. Watch one invenio rollout (surge fits 7Gi) + two 12h Discord windows for silence.
5. Coordinate with alert-hpa worker before any HPA max raise (7Gi fits 2→3 with 1.1Gi slack;
   do not lower invenio further without their sign-off).
6. Follow-ups NOT in this change set: Discord namespace rendering, ouroboros existence check.

Rollback: revert the quota commit(s); ArgoCD self-heals. Raising quotas is non-blocking.
