# HPA maxed-out + metrics API flapping — diagnosis plan (2026-09-27)

Branch: `agent/alert-hpa-metrics` · Alerts: `KubeHpaMaxedOut` (invenio-web-hpa, invenio-worker-hpa, 15m, ~6am/6pm) + `KubeAggregatedAPIDown` (v1beta1.metrics.k8s.io, 80% over 10m)

## Status: FIX-READY CHANGESET STAGED, UNMERGED — live gates open

Worker report: `WORKER-REPORT.md` (worktree root, §8 holds the exact diff + verification output). This plan tracks the follow-through.

## Findings (manifest evidence)

- HPA ceiling `max: 2`, CPU 70% + memory 80%, no `behavior:` — hair-trigger + sticky (memory doesn't scale down linearly).
- Web Deployment pins static `replicas: 2` (= max) while HPA min is 1 — HPA can never scale up; worker omits `replicas` (inconsistent).
- Absolute targets tiny: web 175m/410Mi, worker 350m/615Mi per pod.
- Probe burn (#127, `46720d4`) verified fixed (workers 1m/35%); recurrence = real periodic load or metrics gap, not probe regression. Same plan flagged web memory 77%/80% residual.
- Both firing alerts are upstream kube-prometheus-stack defaults — not in `k8s/infra/monitoring/alerts.yaml`; fix capacity/health, don't silence.
- No metrics-server/APIService in repo (RKE2 kube-system addon); repo netpols don't cover kube-system. Metrics fix is live triage.
- Quota (`requests.cpu: 6`, pods 30) headroom unverified live; quota edits owned by the other worker — gate HPA max raise on their sign-off.

## Proposed change (pending live proof, see WORKER-REPORT.md §3–§5)

1. `invenio-hpa.yaml`: web min 1→2 / max 2→4, worker max 2→4, add `behavior:` (scaleUp 60s / scaleDown 300s). Option B: CPU-only or mem 80→90 if memory-wedged.
2. `invenio-deployment.yaml`: remove static `replicas: 2` (HPA owns it).
3. No quota/limitrange edits (other worker). No metrics-server manifests (RKE2 live-only). No alert silencing.

## Decision log (2026-09-27 changeset)

- Applied: web min 1→2 / max 2→4, worker max 2→4, `behavior:` on both (scaleUp 60s, scaleDown 300s); removed static `replicas: 2` from web Deployment. Verified: `kustomize build` OK, `yamllint` clean.
- Deferred (ambiguous, recorded in WORKER-REPORT.md §8): memory-metric drop/raise (Q1), quota sign-off for max 4 (Q2 — static math fits ≈3.35/6 req, live usage unknown), 6am/6pm driver fix (Q3), metrics-server patch path (Q4, live-only), ArgoCD replicas `ignoreDifferences` (Q5 — watch first sync, add only if it flaps).
- Untouched by design: quota/limitrange (other worker), metrics-server manifests (RKE2 live-only), upstream alert rules (no silencing).

## Operator live-proof checklist (needs VPN)

- [ ] `describe hpa` both: which metric pins? any `FailedGetResourceMetric` during metrics dips?
- [ ] `top pods` web/worker at peak vs threshold math above.
- [ ] `get apiservice v1beta1.metrics.k8s.io -o yaml` + metrics-server pods/logs/events in kube-system.
- [ ] Scheduler/worker logs around 6am/6pm + Velero/CNPG schedule overlap + Traefik traffic check.
- [ ] `describe resourcequota/limitrange` in invenio before approving max 4.
- [ ] Post-change: two 6am/6pm windows without 15m pin; metrics API Available=True steady; ArgoCD green.

## Open questions (see WORKER-REPORT.md §6)

Q1 driving metric per HPA (CPU vs memory) → metric choice. Q2 approved max (quota sign-off). Q3 6am/6pm driver (beat vs backup vs traffic). Q4 RKE2 metrics-server patch path + runbook-annotation policy. Q5 replicas-field removal vs ArgoCD diff behavior.

## Rollback

Zero manifest changes on this branch — nothing to roll back. Future §3 merge reverts via `git revert` + ArgoCD sync; manual clamp `kubectl scale deploy/invenio-web --replicas=2` if quota wedges.

## Live verification — post-merge 218235e (2026-09-27, VPN, operator)

- [x] `describe hpa` both: **memory is the driving metric** (ScalingActive=True on memory, CPU ~0-1m idle). No `FailedGetResourceMetric`. ScalingLimited=False both.
- [x] `top pods`: web 487Mi (25d-old pod, 95% of request) + 235Mi (31m-old post-merge pod) = 70% avg; worker 389/402Mi = 51%. **Web pods show slow memory growth over weeks** — follow-up: watch for leak vs normal uwsgi growth.
- [x] apiservice `v1beta1.metrics.k8s.io` Available=True, raw metrics API responds. Backend is `rke2-metrics-server` (single replica, 9 restarts, 71d old) — SPOF explains past 80% flapping. Follow-up: 2 replicas out-of-band (RKE2 config, not repo).
- [x] Post-merge sync clean: setup Job completed, new web pod Running, no ArgoCD diff-fighting on removed `replicas:` field.
- [ ] Still open: two 6am/6pm soak windows without 15m max-pin; 6am/6pm driver (beat logs only show start 2026-09-23 — inconclusive).
