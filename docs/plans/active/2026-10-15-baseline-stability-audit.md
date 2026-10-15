# Thesis Baseline Stability Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` in this session. Delegated execution goes through herd with isolated worktrees.

**Goal:** Assess cluster and Invenio stability and remove agent-planning references from maintained code and user-facing documentation without changing behavior.

**Architecture:** The lead collects live read-only evidence. One worker reviews deployment configuration; another performs isolated text cleanup. Findings converge into a reviewed assessment before any production remediation.

**Tech Stack:** RKE2, Kubernetes, ArgoCD, InvenioRDM, PostgreSQL, Redis, OpenSearch, Prometheus, GitHub Actions.

## Global Constraints

- Approved by the operator on 2026-10-15; tracking issue #137.
- No cluster mutations, load tests, dependency downloads, or secret access.
- Preserve existing local changes in `.opencode/agents/`.
- Preserve planning history under `docs/plans/`, technical meaning, and executable behavior.
- No generated/vendor edits or speculative refactors.
- Every finding records severity, evidence, impact, proposed response and uncertainty.

## Ownership

| Owner | Files | Scope |
|---|---|---|
| Lead | This plan; `docs/plans/README.md` | Cluster evidence, consolidated assessment, verification |
| invenio-audit | `docs/plans/active/2026-10-15-invenio-deployment-review.md` | Read-only configuration audit; no source edits |
| planning-cleanup | Maintained comments and user-facing docs outside `docs/plans/` and `.opencode/` | Agent-planning language cleanup only |

Worker reports remain uncommitted scratch files and are not integrated.

## Execution

- [x] Read repository conventions, preserve local edits, fetch origin and create issue.
- [x] Run baseline YAML lint and selector validation: passed with existing line-length warnings.
- [x] Create isolated worker and integration worktrees from `origin/main`.
- [x] Dispatch bounded workers with ownership and escalation rules.
- [x] Inspect node/pod conditions, recent warning events, ArgoCD, PVCs and dependency health.
- [x] Trace scheduler/database and monitoring/control-plane restarts using timestamped logs and historical metrics; initiating PostgreSQL termination cause remains unknown.
- [x] Inspect backups, scrape coverage, storage utilization and non-mutating application health endpoints.
- [x] Record baseline blockers and gaps; do not equate current readiness with historical stability.
- [x] Review cleanup candidate matches manually, then independently verify semantics and applicable lint/render checks.
- [ ] Consolidate non-overlapping changes, inspect full diff, open PR and wait for required CI.
- [ ] Report production remediation separately for approval; no deployment changes in this audit.

## Baseline Acceptance

- Freeze Git revision, image digests, resources, replica/HPA settings, dataset and background job schedules.
- Observe a representative 24-hour window including scheduled jobs; explain all restarts and monitoring gaps.
- Verify no sustained node/storage pressure, unexpected crashes, or growing task backlog.
- Establish repeatable latency/error/throughput measurements with an agreed workload before declaring baseline ready.
- Confirm backup freshness and distinguish successful backup creation from tested recovery.

## Findings

**Baseline verdict: not qualified.** The application is serving, but control-plane stalls and backup verification gaps remain. Evidence below was collected read-only on 2026-10-15 around 12:00–12:15 UTC from context `btd`; historical metrics cover the preceding 24 hours where indicated. Prometheus retains seven days and itself restarted, so absence of a metric is not absence of a fault.

### HIGH — Confirmed control-plane storage stalls and API disruption

- Etcd logs report `slow fdatasync`: 10.452 seconds at 07:57:15Z and 7.643 seconds at 07:57:23Z. A bounded six-megabyte log sample since 07:45Z contained 123 slow-fdatasync warnings, 395 ReadIndex retries and 1,526 slow-apply warnings. These are sample counts, not complete incident totals.
- At 07:57:23Z the cloud controller lost its leader lease after localhost API requests timed out. CSI controllers, image updater and other controllers also restarted around this incident. Recent API-server readiness/liveness events explicitly identify etcd checks failing.
- PromQL `sum(increase(apiserver_request_total{code=~"5.."}[24h]))` returned approximately 109; extrapolated counter increases are not exact integer incident counts.
- Etcd uses the control-plane host directory `/var/lib/rancher/rke2/server/db/etcd`, not an application PVC. Underlying host disk latency, virtual-machine pauses and storage contention remain hypotheses; application NFS latency is not proven to cause etcd stalls.
- **Response:** university/host administrator should inspect disk latency, hypervisor/storage contention, swapping and kernel logs at those timestamps. Do not increase application CPU or merely relax probes as a substitute. Verify a representative quiet window without slow fsync, lease losses or API readiness failures.

### HIGH — Velero scheduled recovery is not verified

- Fully qualified `backups.velero.io` returned zero objects cluster-wide. Weekly schedule reports last run 2026-10-11T03:00:20Z, which proves scheduling activity, not successful recovery data.
- `velero_backup_total` is zero. `velero_backup_last_successful_timestamp` is `1788703863` (2026-09-06); the configured stale alert instead reads nonexistent `velero_backup_timestamp`, so its expression returns no series.
- Server logs repeatedly attempt to synchronize September backups. Expired August objects are recreated and deletion requests report `backup is still in progress`. Root cause of disappearing/incomplete Backup resources is unresolved; plugin process-exit messages alone do not establish a plugin crash.
- This reproduces an older open concern in `2026-09-03-architecture-and-dr.md`; it is not a newly proven regression. No restore objects were present; this does not prove nobody ever tested recovery elsewhere.
- **Response:** inspect object metadata and controller ownership/audit history with separately approved credential access if needed; correct freshness monitoring with absence detection, then verify a full backup and isolated restore. No deletes or new backup/restore jobs were run in this audit.

### MEDIUM — Missing host observability on the control plane

- All 31 discovered scrape targets were up, but node-exporter runs on only the two workers. The control-plane node has `CriticalAddonsOnly=true:NoExecute`; the live exporter tolerates `NoSchedule` only.
- `k8s/infra/monitoring/values.yaml` places broad tolerations under `nodeExporter`, while the live `prometheus-node-exporter` DaemonSet lacks the needed NoExecute toleration. The intended subchart configuration must be render-verified before changing values.
- Etcd, scheduler and controller-manager scraping is explicitly disabled because their endpoints bind localhost. Green discovered targets therefore do not mean complete coverage.
- **Response:** make a separately reviewed, render-tested exporter toleration correction; choose a secure localhost metrics collection approach rather than exposing control-plane endpoints indiscriminately.

### MEDIUM — PostgreSQL termination causes scheduler failure

- Previous PostgreSQL logs received a termination signal at 2026-10-14T01:35:36Z and requested smart shutdown at 01:35:47Z. The container finished with exit 0 at 01:38:58Z and restarted. Why the signal was sent is unknown; current event retention does not establish its initiator.
- Scheduler exited at 01:39:32Z with SQLAlchemy/psycopg2 `SSL connection has been closed unexpectedly`, then restarted and logged Beat startup at 01:40:23Z. This establishes a failure sequence, not proof that CPU throttling caused it.
- Scheduler has nine lifetime restarts; web and worker containers currently show zero. Neither process presence nor quiet Beat logs prove scheduled jobs are being delivered.
- **Response:** trace the restart initiator with historical kubelet/audit logs and review database connection recycling/retry behavior. Validate scheduled task delivery and recovery in an isolated test before changing production behavior.

### LOW / impact unverified — Scheduler throttling ratio

- Live rule is `increase(throttled_periods[5m]) / increase(periods[5m]) > 0.25` for 15 minutes, severity `info`; the supplied Discord message says WARNING. Notification severity mapping remains unverified.
- Ratio was 29.41%; five-minute CPU usage approximately 1.1m with sampled 24-hour peak approximately 1.31m against a 500m cap. Sampling at five-minute resolution can miss shorter peaks.
- Direct cgroup read: `cpu.max=50000 100000`; `nr_periods=7346`, `nr_throttled=2265`, `usage_usec=145317927`, `throttled_usec=101591873`. Those are lifetime counters for the current container, not current latency or a 29% CPU-time loss. Prometheus returned no `container_cpu_cfs_throttled_seconds_total` series for this container.
- **Response:** correlate short CPU bursts with actual scheduling delay before changing its cap. Preserve raw evidence and distinguish noisy ratio alerts from demonstrated baseline interference.

### MEDIUM — Backup cadence differs from its documented intent

- `k8s/apps/invenio-deps/postgresql/cluster.yaml` describes a daily 02:00 backup and supplies `schedule: "0 2 * * *"`. The live ScheduledBackup has that same string, but status reports last run 12:02Z and next run 13:02Z; completed Backup objects establish hourly execution at minute 02.
- This is a schedule-interpretation/documentation mismatch, not manifest drift. The desired cadence must be chosen before fixing cron syntax or documenting the existing hourly policy. Keep backup activity fixed and recorded across experiments; do not silently reduce recovery coverage.

### Current health and residual risks

- All 17 ArgoCD Applications are Synced/Healthy; all active pods were ready at inventory time and all PVCs Bound. Nodes reported no memory/disk/PID pressure. These are point-in-time observations.
- Public UI and API `/ping` returned HTTP 200 (single requests, approximately 4.06s and 0.75s respectively); no load test or latency SLO certification was performed.
- CNPG has one ready instance; continuous archiving and last backup conditions are true. Hourly backups through 12:02Z completed, with first recoverability point 2026-09-15T12:02:08Z. Backup completion is not a restore test or off-site protection.
- OpenSearch is yellow with one node, 76 active primary shards, 73 unassigned shards and no pending tasks. Missing replicas are consistent with a single-node topology; allocation explanations were not queried, so other causes are not excluded.
- Worker node root filesystems have approximately 56% and 61% available. NFS-backed volume metrics all show roughly 39% available and likely describe a shared filesystem rather than per-PVC enforced quotas; do not sum them as independent capacity.
- Worker five-minute iowait maxima were approximately 12% and 31% over 24 hours. Steal was zero for the two monitored workers only; no host-level control-plane history is available from node-exporter.
- Control plane, PostgreSQL, Redis, OpenSearch and in-cluster backup storage include single failure domains. Availability/recovery requirements must be stated rather than calling this deployment highly available.
- All three nodes still report the same machine ID, although their system UUIDs differ. This confirms the older identity-hygiene concern, not that the collision caused today's etcd stalls; any host identity correction requires administrator coordination.
- Redis reports `allkeys-lru`, approximately 260MiB used against 512MiB maxmemory, and zero `evicted_keys` in its current process lifetime. Broker eviction is a configuration risk, not observed message loss.
- Three old Retain PVs remain Released (`postgres-1`, `postgres-2`, old `redis-data`). No PV/NFS data was deleted; retention may be deliberate and needs an owner decision before cleanup.

## Cleanup Verification

The cleanup worker changed eight files: three historical cluster assessments and five YAML comment blocks. No matching agent-phase/task/checkpoint pollution was found in `docker/ugm`; legitimate asynchronous task names and ArgoCD sync waves were retained. Planning history, `.opencode/` configuration and the operator's pre-existing edits are excluded. The removed `.superpowers/.../task-3-report.md` link did not resolve to a tracked file.

Lead verification independently passed: whole-repository infrastructure YAML lint (existing line-length warnings), parsed YAML equality and exact non-comment-line equality for all five changed YAML files, monitoring validation (four dashboards, 22 alerts), selector validation, security Kustomize render and whitespace validation. Worker changed-file sets do not overlap. Full Helm rendering was deliberately not run locally because the script can download missing tools/charts and rebuild generated output; PR CI provides that check. CI status is recorded in the PR rather than inferred from local checks.

## Evidence Commands

All commands were read-only; avoid using ambiguous resource short names such as `cluster` or `backups` in this cluster.

```sh
kubectl get events -A --field-selector type=Warning --sort-by=.lastTimestamp
kubectl -n argocd get applications
kubectl -n kube-system logs etcd-ubuntu-btd-kubernetes-server --since-time=2026-10-15T07:45:00Z --limit-bytes=6000000
kubectl -n kube-system logs cloud-controller-manager-ubuntu-btd-kubernetes-server --previous
kubectl -n database logs postgres-3 -c postgres --previous --since-time=2026-10-14T01:30:00Z
kubectl -n database get clusters.postgresql.cnpg.io postgres -o json
kubectl get backups.velero.io -A
kubectl -n velero get schedules.velero.io
kubectl -n velero logs velero-55fdb8b9c-glvtc -c velero --tail=100
kubectl -n monitoring get ds monitoring-prometheus-node-exporter -o json
```

PromQL was queried through localhost inside the existing Prometheus container using `wget`; the API-server Service proxy timed out, so it was not repeatedly retried. Additional useful expressions: `count by(job)(up)`, `velero_backup_last_successful_timestamp`, `velero_backup_timestamp`, `sum by(namespace,pod,container)(increase(kube_pod_container_status_restarts_total[24h])) > 0`, and `sum by(namespace,pod,probe_type)(increase(prober_probe_total{result="failed"}[24h])) > 0`.
