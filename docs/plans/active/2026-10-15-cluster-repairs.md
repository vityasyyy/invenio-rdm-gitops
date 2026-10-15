# Cluster Reliability Repairs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: `executing-plans`. Delegation uses herd with separate worktrees.

**Goal:** Prepare tested fixes for Velero ownership and monitoring faults while investigating post-storage-migration health.

**Architecture:** The lead investigates live conditions read-only. Independent workers prepare ownership safeguards and monitoring repairs. Production changes require explicit approval after review and CI.

**Tech Stack:** ArgoCD 2.12, Velero 1.17, Prometheus, Helm, Kubernetes/RKE2, Python validation scripts.

## Constraints

- Issue #139; two workers approved by the operator on 2026-10-15.
- No merge, deployment, cluster mutation, backup/restore job, host identity change, destructive cleanup, secret access or dependency download in this batch.
- Preserve existing edits in the original checkout's `.opencode/agents/`.
- No blanket pruning disablement, untested tracking migration or alert suppression.
- Infrastructure configuration is exempt from application TDD, but regression checks must demonstrate each fault before its fix and pass afterward. Maintained test/helper code follows TDD.
- No agent phase/task references in maintained source comments or commit messages.

## Ownership

| Owner | Files | Deliverable |
|---|---|---|
| velero-repair | `k8s/infra/argocd/`, `k8s/infra/velero/`, `scripts/ci-validate-velero.py`, its dedicated test, `docs/plans/active/2026-10-15-velero-ownership.md` | Narrow protection for both generated and storage-resynchronized Backup resources |
| monitoring-repair | `k8s/infra/monitoring/`, `scripts/ci-validate-monitoring.sh`, dedicated monitoring test files, `docs/plans/active/2026-10-15-monitoring-repairs.md` | Stale/absent backup alert and control-plane node-exporter toleration repair |
| Lead | This plan, existing baseline assessment and plans index, CI workflow wiring if needed | Live findings, integration and independent verification |

Workers must not edit overlapping files or the plans index. Their scratch reports remain uncommitted.

## Work

- [x] Fetch origin, preserve user edits and create isolated worktrees from `d6b4458`.
- [x] Baseline monitoring validation and YAML lint pass (existing warnings only).
- [x] Dispatch bounded workers and review both designs and regression evidence.
- [x] Verify etcd/API health after IT storage migration; distinguish old events from current faults.
- [x] Correlate worker-02 probe failures, I/O pressure, PostgreSQL restarts and workload logs; underlying worker storage cause remains unresolved.
- [x] Review worker changes and compatibility against deployed versions; live success remains unverified.
- [x] Integrate non-overlapping source changes into separate PRs; wire regression checks into existing CI without new downloads.
- [x] Run applicable local tests, YAML lint, monitoring validation, cached renders and full diff review. Full remote-base rendering and selector checks run in CI.
- [x] Open scoped PRs #141 (Velero) and #142 (monitoring); check outcomes live in GitHub. Full ArgoCD remote-base rendering was not attempted locally.
- [ ] Ask for production approval before merge. Subsequent verification must include surviving Backup resources, alert evaluation, three-node exporter coverage and post-merge pipelines.

## Known Evidence and Disposition

- ArgoCD explicitly prunes re-created Backups bearing `app.kubernetes.io/instance=velero`; Velero copies Schedule labels when template labels are absent. Protect old metadata as well as future generated resources.
- Stale alert uses absent `velero_backup_timestamp`; live metric is `velero_backup_last_successful_timestamp`. Include missing-series regression cases, not only a metric rename.
- Live node-exporter tolerates NoSchedule only; the master has `CriticalAddonsOnly=true:NoExecute`. Configure the actual subchart and verify rendered output.
- IT migrated master storage. At 14:51 UTC, the preceding 30-minute etcd sample had no warnings/slow syncs and host I/O pressure averages were zero. Longer observation is still needed.
- Duplicate machine IDs remain hygiene debt, not a demonstrated cause of Rancher failure. No reboot is justified by current evidence.
- Velero/AWS-plugin version pairing requires investigation; do not bundle an image update without evidence, compatibility review and approval for any required downloads.

## Rollback and Live Acceptance

Revert the smallest scoped PR if its deployment regresses health. Ownership protection must retain Schedule management and existing unrelated ArgoCD exclusions; test old labeled Backups as well as new ones. Monitoring changes must preserve existing alert routing. No claim of recoverability until a separately approved backup and isolated restore are verified.

## Follow-up Evidence (14:57–15:00 UTC)

- The bounded 90-minute etcd sample contains older warnings, but the latest warning was 14:12:40Z. Do not mistake the larger historical count for a post-migration regression. Exact migration completion time is still unknown.
- Recent 30-minute API 5xx increase is zero; one-hour container restart increase is empty. Current PostgreSQL, Redis and Celery worker restart counts are unchanged. These are short-window checks, not long-term stability proof.
- Worker-02 host I/O PSI full avg300 was approximately 2.46%; a later Prometheus five-minute full-stall rate was approximately 1.61%. Worker-01 was approximately 0.012%. Master host I/O PSI averages were zero. Worker-02 remains worth observing; the backend/disk responsible has not been identified.
- Recent 30-minute probe failures contained only one Loki canary readiness failure; earlier PostgreSQL/Redis/Celery events predate this window. PostgreSQL logged a TLS probe handshake timeout at 14:16:44Z. Redis RDB saves completed successfully every approximately five minutes, taking around four to seven seconds in sampled logs; this does not prove they caused prior stalls.
- Worker liveness invokes Celery with an internal 30-second timeout but Kubernetes kills the probe after 25 seconds; readiness uses 15/25 seconds. App imports add startup cost. This inconsistency is recorded, not fixed by silently relaxing probes during a storage investigation.
- **New confirmed logging fault, tracked in #140:** Loki is Healthy/Synced but has no server workload or gateway Service, only canaries and caches. Canary logs repeatedly fail DNS lookup of the nonexistent gateway. Pinned chart 6.24.0 defaults `deploymentMode: SimpleScalable`; repository values configure singleBinary replicas without selecting that mode and set scalable replicas to zero. Collector installation and endpoint consistency need a separately reviewed design; do not disable canaries to hide it.
- Machine-ID duplication is host hygiene debt, not demonstrated Kubernetes node identity or Rancher-session failure. IT recommends avoiding unnecessary disruptive reboots; retain the IDs pending evidence of actual impact.

## Reviewed Velero Change

The ownership worker committed one local-cluster exclusion for `velero.io` `Backup` and `Restore`; `Schedule` remains managed. Six regression checks and Velero Kustomize render independently passed during lead review. CI now runs the validator and its tests in the existing YAML job, reusing its PyYAML installation. No new tools or downloads are required locally. Full ArgoCD overlay rendering remains a CI check because the remote base is not locally cached. This PR does not deploy the monitoring worker's independent changes.

The monitoring PR independently passes production-rule synthetic cases, reproduction against the old rules, rule syntax, YAML/shell checks, monitoring Kustomize render and cached chart rendering of both exporter tolerations. The PRs overlap only in the plans index; preserve both rows when integrating. No deployment approval has been granted.
