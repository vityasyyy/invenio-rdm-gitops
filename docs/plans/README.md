# Plans Index

Plans live in `docs/plans/` — `active/` for work that still has an owner action,
`completed/` for merged, executed or reviewed work. Current cluster facts live in
[`docs/current-state.md`](../current-state.md); open issue tracking remains in GitHub.

## Active

| Plan | Status |
|---|---|
| [Codebase improvements](active/2026-05-19-codebase-improvements.md) | Most tasks merged. Open: released-PV cleanup, independent/off-site backup, and the worker-02 kubelet/IT dependency. |
| [Email confirmation / SMTP](active/2026-09-02-email-confirmation.md) | Mail plumbing deployed, but the live host is still `smtp.example.org`; university relay credentials, CA and firewall details are pending. |
| [Architecture and DR findings](active/2026-09-03-architecture-and-dr.md) | Failure-mode/DR roadmap. Independent MinIO/NFS recovery and host-maintenance prevention remain open. |
| [Worker-02 incident](active/2026-10-16-worker02-incident.md) | Software and alert work merged; university IT restart-policy prevention remains external under #145. |

## Completed

These are historical execution/review records, not current cluster instructions.

| Plan | Evidence |
|---|---|
| [2026-05-20-ci-cd-and-reconciliation](completed/2026-05-20-ci-cd-and-reconciliation.md) | Phase A/B/C/D done — AppProject whitelist, Discord bridge image fix, CI/CD pipeline rewrite, branch protection, generated `rendered/` |
| [2026-05-25-cluster-health-fixes-and-improvements](completed/2026-05-25-cluster-health-fixes-and-improvements.md) | Tasks 1–14 done — PDB selectors, HPA drift, probes, cloudflared seccomp, quotas, orphan cleanup, scrape fixes |
| [2026-05-29-cluster-health-fixes](completed/2026-05-29-cluster-health-fixes.md) | Tasks 1–5 done — CNPG egress, Loki cache sizing, worker startup probe, Velero LimitRange, ArgoCD ignoreDifferences |
| [2026-06-01-traefik-404-fix-overhaul](completed/2026-06-01-traefik-404-fix-overhaul.md) | Tasks 1–11 done — Traefik egress, default-deny ingress, shared middleware, IngressRoutes, README overhaul |
| [2026-08-14-cluster-recovery-and-ugm-migration](completed/2026-08-14-cluster-recovery-and-ugm-migration.md) | Cluster recovered, UGM image vendored/built/deployed; API regression fixed |
| [2026-09-05-backup-scheduler-deadlock](completed/2026-09-05-backup-scheduler-deadlock.md) | Scheduler deadlock cleared; first completed backup since June; loop recovered |
| [2026-09-05-worker-02-rebalance](completed/2026-09-05-worker-02-rebalance.md) | Resource cuts rolled out; acceptance met; kopia placement follow-up noted |
| [fix-cluster-health](completed/fix-cluster-health.md) | Kustomize patch format, AppProject/Application bootstrap fixed |
| [fix-invenio-upload-and-minio-buckets](completed/fix-invenio-upload-and-minio-buckets.md) | Buckets/uploads/store path fixed; later storage layout superseded by MinIO file store |
| [2026-09-08-monitoring-overhaul](completed/2026-09-08-monitoring-overhaul.md) | Dashboards, alerts, native Discord receiver and CI gates completed |
| [2026-09-14-monitoring-scrapes](completed/2026-09-14-monitoring-scrapes.md) | Velero/cloudflared/OpenSearch/MinIO/Traefik scrape wiring completed; follow-ups landed |
| [2026-09-15-monitor-noise-cleanup](completed/2026-09-15-monitor-noise-cleanup.md) | Localhost-only scrapes disabled; Watchdog/InfoInhibitor blackholed; live proof done |
| [2026-09-15-worker-probe-burn](completed/2026-09-15-worker-probe-burn.md) | Exec-probe burn fixed; worker CPU/HPA recovered; live proof done |
| [2026-09-02-db-recovery](completed/2026-09-02-db-recovery.md) | Executed and verified 2026-09-05; native recovery later proven on 2026-10-25 |
| [2026-09-02-proxy-502](completed/2026-09-02-proxy-502.md) | Resolved; worker-02 restored by IT; machineID/restart risk moved to architecture/DR |
| [2026-09-15-followup-sync](completed/2026-09-15-followup-sync.md) | Docs sync for #113/#115/#118; #119/#122 merged and live-proofed |
| [2026-09-15-infra-scrape-gaps](completed/2026-09-15-infra-scrape-gaps.md) | Merged #122; live scrape proof recorded |
| [2026-10-15-baseline-stability-audit](completed/2026-10-15-baseline-stability-audit.md) | Read-only assessment completed; current facts superseded by current-state; follow-ups in #139/#145 |
| [2026-10-15-cluster-repairs](completed/2026-10-15-cluster-repairs.md) | Repairs merged/verified; recovery follow-ups in #139 |
| [2026-10-15-invenio-deployment-review](completed/2026-10-15-invenio-deployment-review.md) | Static review completed; findings tracked by later plans and current-state |
| [2026-10-15-monitoring-repairs](completed/2026-10-15-monitoring-repairs.md) | Merged/verified; stale-backup alert exposed #139 |
| [2026-10-15-runtime-selection](completed/2026-10-15-runtime-selection.md) | Wiring verified live; backup recoverability tracked in #139 |
| [2026-10-15-velero-ownership](completed/2026-10-15-velero-ownership.md) | Ownership protection verified; backup freshness tracked in #139 |
| [2026-10-16-alert-truth](completed/2026-10-16-alert-truth.md) | Rules/formatting merged and verified; current alert routing/state in current-state and #145 |
| [2026-10-16-recovery-logging-review](completed/2026-10-16-recovery-logging-review.md) | Read-only assessment; Loki/Alloy implementation tracked in #140 |
| [2026-10-19-backup-repair](completed/2026-10-19-backup-repair.md) | Merged #147; post-merge checks passed; weekly evidence in #139 |
| [2026-10-19-drill-readiness](completed/2026-10-19-drill-readiness.md) | Canary/file mechanism verified; native DB proof completed 2026-10-25 |
| [2026-10-19-native-recovery-preflight](completed/2026-10-19-native-recovery-preflight.md) | Scoped policies/credential executed; native proof completed |
| [2026-10-19-native-recovery-proof](completed/2026-10-19-native-recovery-proof.md) | Native recovery verified 2026-10-19 and re-verified 2026-10-25; seeded app consistency remains in #139 |
| [2026-10-19-recovery-batch1](completed/2026-10-19-recovery-batch1.md) | Repair deployed/verified; native recovery completed; seed/app consistency in #139 |
| [2026-10-19-restore-contract](completed/2026-10-19-restore-contract.md) | Design executed by isolated drill; current runbook in current-state/operations |
| [2026-10-20-drill-closeout](completed/2026-10-20-drill-closeout.md) | Drill namespaces removed; Retain PVs and MinIO-key revocation remain owner/storage actions |
| [2026-10-20-recovery-closeout-wave](completed/2026-10-20-recovery-closeout-wave.md) | Closeout deployed; temporary access removed |
| [2026-10-20-required-backup-monitoring](completed/2026-10-20-required-backup-monitoring.md) | Deployed #154/#155; long-term delivery evidence remains in #139 |

## Historical assessments

Dated cluster assessments remain in `docs/cluster-assessment-*.md`. They describe
past observations and are not current deployment instructions.
