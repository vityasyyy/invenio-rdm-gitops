# Worker probe burn — lighten celery exec probes (#127)

## Problem

`invenio-worker` pods burned 677–838m CPU with zero workload: celery
active/scheduled/reserved all empty on both nodes, broker `LLEN celery` = 0,
2 log lines in 24h (INFO on), pods stable 13 days / 0 restarts. This drove
`CPUThrottlingHigh` (~30–35%) and `invenio-worker-hpa` maxed (186%/70%).

## Root cause (measured live 2026-09-15, no guessing)

Each kubelet exec probe (`celery inspect ping`) re-imports the whole app:
timed at 11.5s wall / ~11.4s CPU per run. Steady-state cadence was readiness
every 30s (380m) + liveness every 60s (190m) = **~570m/pod self-inflicted**,
~75% of observed burn. Second bug: readiness `timeoutSeconds: 35` exceeded
`periodSeconds: 30`, letting probe runs overlap and pile up.

Scaling (limits/replicas) was rejected: it would feed the loop, not fix it.

## Fix

`k8s/apps/invenio/invenio-worker-deployment.yaml` only:
- readiness: period 30→120s, timeout 35→25s, threshold 3
- liveness: period 60→300s, timeout 45→25s, threshold 3
- startup unchanged. Worst-case detection: unready ~6 min, hang ~15 min —
  acceptable for task workers (no live traffic; dead parent restarts instantly
  via container exit).

Expected: probe burn ~570m→~135m/pod; HPA util back under 70%; both alerts
resolve. No quota/replica/overcommit impact.

## Follow-ups (not this change)

- Cheap-signal probes (pid check) instead of full app import — needs
  entrypoint/image work.
- `invenio-web-hpa` maxed (memory 77%/80% at 2/2): observe; web pods are
  small (1m CPU) — raise maxReplicas if traffic-driven, separate decision.

## Lead live-proof — DONE 2026-09-15 (VPN, post-merge `46720d4` + Deploy Verify green)

Rollout clean (new RS, old pods drained, 0 restarts). New pods carry
liveness 300/25 + readiness 120/25 (verified in live spec).

1. DONE — `kubectl top`: workers 677/838m → **1m/1m** each (~10 min
   post-Ready; far better than the ~135m estimate — the 11.4s measured
   probe cost included one-off import overhead).
2. DONE — worker HPA util 186% → **35%** (memory 48%).
3. DECAYING (seen mid-path, deterministic) — `CPUThrottlingHigh` already
   `pending` on one instance, `KubeHpaMaxedOut` still firing on windowed
   history; no driving series remain (1m pods), so both clear as `for:`
   windows roll past the fix (~15 min). Scale-in to 1 replica expected on
   HPA stabilization.
4. DONE — ArgoCD all Synced+Healthy; Deploy Verify green.

## Lead live-proof procedures (needs VPN) — superseded by above

1. Post-sync: `kubectl top pods -n invenio` — workers well under pre-change
   677/838m within ~10 min (old pods replaced by rollout).
2. `invenio-worker-hpa` util < 70% and replicas scale in if idle.
3. `CPUThrottlingHigh{namespace="invenio"}` + worker `KubeHpaMaxedOut`
   resolve (stale `for:` windows may take ~15 min).
4. ArgoCD all Synced+Healthy; Deploy Verify green; rollout completes with
   zero task loss (celery drains on SIGTERM; watch `inspect active` empty
   before old pods terminate — rolling update default).
