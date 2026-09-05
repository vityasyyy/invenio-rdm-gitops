# Plans Index

Plans live in `docs/plans/` — `active/` for ongoing work, `completed/` for
plans whose work is merged/verified. Every PR that changes behavior updates
the relevant plan and this index (docs-follow-code). New concerns/findings
discovered during a wave go into `docs/plans/active/` as part of the wave's PR.

## Active

| Plan | Branch | PR | Status |
|---|---|---|---|
| [2026-09-03-architecture-and-dr.md](active/2026-09-03-architecture-and-dr.md) | — | — | **Active** — architecture deep-dive + failure-mode matrix + DR findings (InvenioRDM vs DBRepo, data plane, 10 findings, restore-drill roadmap). 2026-09-05: worker-02 streaming restored (IT restart), postgres 1/1/1 Ready+Archiving True. Operator actions still pending (sealed-secrets key backup, machineID IT ticket — flap risk remains, off-site storage decision, SMTP creds) |
| [2026-09-05-worker-02-rebalance.md](active/2026-09-05-worker-02-rebalance.md) | — | #74 | **Active** — worker-02 rebalance (issue #74, T2). 2026-09-05 baseline after zombie removal: worker-02 83% requests / 354% limits, worker-01 67% / 187%. Right-size + rebalance pending |
| [2026-09-05-backup-scheduler-deadlock.md](active/2026-09-05-backup-scheduler-deadlock.md) | — | #81 | **Diagnosed 2026-09-05** — scheduler frozen since 2026-08-21 (`already exists` on `postgres-daily-backup-20260821030200`), 1,872 failed / 0 completed. Streaming healthy again (IT restart); unblock commands prepared, awaiting lead approval |
| [2026-09-02-db-recovery.md](active/2026-09-02-db-recovery.md) | fix/76-db-recovery | #77 | **EXECUTED + VERIFIED 2026-09-05** — WALs moved aside (42 objects, reversible), archiver unblocked, operator reconciled to **spec 1 / status 1 / ready 1**, postgres-1/2 removed (PVs now Released — see Task 16), conditions Ready True + ContinuousArchiving True. LastBackupSucceeded still False — backup scheduler deadlock owned by backup-diag worker (`2026-09-05-backup-scheduler-deadlock.md`) |
| [2026-09-02-proxy-502.md](active/2026-09-02-proxy-502.md) | fix/75-proxy-502 | #78 | **RESOLVED 2026-09-05** — university IT killed + restarted worker-02 then reconnected the agent session; logs/exec/healthz to worker-02 all OK ×3. Root cause confirmed as dropped remotedialer session. **machineID collision (all 3 nodes `de88ca16...`) remains — flap risk remains, IT ticket still needed** |
| [2026-09-02-email-confirmation.md](active/2026-09-02-email-confirmation.md) | feat/67-mail-config (#69) + fix/71-image-digest-bump (#72) | #69, #72 | **DEPLOYED + VERIFIED 2026-09-02, still placeholder 2026-09-05** — SMTP plumbing live in cluster (ConfigMap relay vars, sealed MAIL_USERNAME/MAIL_PASSWORD, mail-enabled image `0f685be` rolled to web/worker/scheduler and pinned in git + live). Awaiting operator-supplied university SMTP relay credentials (re-seal + replace placeholder host `smtp.example.org`) |
| [2026-05-19-codebase-improvements.md](active/2026-05-19-codebase-improvements.md) | main (merged #9–#36) | — | **Mostly complete** — Tasks 1–15 verified in repo (sync waves, orphan cleanup, probes, Loki, netpol IPs, HPA/PDB, AppProject split, cert-manager removal, alerts). Remaining: Task 16 (released-PVC cleanup — now 3 Released PVs 2026-09-05: postgres-1, postgres-2, old redis-data), Task 17 (kubelet 502 — worker-02 streaming restored 2026-09-05 via IT restart, but machineID collision keeps flap risk; out of scope for manifests), Task 18 (off-site backup — placeholder, needs external S3 provider + credentials) |

## Completed (merged to main)

| Plan | Branch | Merged |
|---|---|---|
| [2026-05-20-ci-cd-and-reconciliation.md](completed/2026-05-20-ci-cd-and-reconciliation.md) | main (merged #9) | Phase A/B/C/D done — AppProject whitelist, Discord bridge image fix, CI/CD pipeline rewrite (`ci-render-manifests.sh`, `ci-validate-selectors.sh`, `validate-infra.yaml`, `deploy-verify.yaml`, `.pre-commit-config.yaml`, `setup-branch-protection.sh`), `rendered/` gitignored |
| [2026-05-25-cluster-health-fixes-and-improvements.md](completed/2026-05-25-cluster-health-fixes-and-improvements.md) | main (merged #9) | Tasks 1–14 done — PDB selectors, HPA drift, probes, cloudflared seccomp, branch protection, resource quotas, Replace=true removal, orphan cleanup, duplicate AppProject removal, prometheus scrape ports, syncOptions, theme ConfigMap removal, Dockerfile pip cache |
| [2026-05-29-cluster-health-fixes.md](completed/2026-05-29-cluster-health-fixes.md) | main (merged #12, #17, #18) | Tasks 1–5 done — CNPG apiserver egress netpol (ports 443+6443), Loki chunks-cache sizing, worker startup probe, Velero LimitRange, ArgoCD-self ignoreDifferences |
| [2026-06-01-traefik-404-fix-overhaul.md](completed/2026-06-01-traefik-404-fix-overhaul.md) | main (merged #24–#36) | Tasks 1–11 done — Traefik apiserver egress fix, default-deny ingress, cross-namespace Middleware, shared security-headers Middleware, IngressRoute updates, duplicate AppProject removal, README overhaul, sealed-secrets key moved out of repo |
| [2026-08-14-cluster-recovery-and-ugm-migration.md](completed/2026-08-14-cluster-recovery-and-ugm-migration.md) | main (merged #54–#64) | **Phase 1 + Phase 2 COMPLETE** — cluster recovered to baseline (all ArgoCD apps Synced+Healthy, 500 saga cleared), UGM image vendored + built (`ghcr.io/vityasyyy/invenio-ugm`), deployed with NFS storage, API regression fixed (combined wsgi), debug artifacts removed. See `docs/planning-ledger` for the full task-by-task record |
| [fix-cluster-health.md](completed/fix-cluster-health.md) | main (merged #9) | Done — kustomize patch format fix (8 patch files), ArgoCD infra project + Application manifests applied, bootstrap completed |
| [fix-invenio-upload-and-minio-buckets.md](completed/fix-invenio-upload-and-minio-buckets.md) | main (merged #49, #59) | Tasks 1–6 done — MinIO buckets created, bucket creation moved into `invenio-setup-job` (PostSync hook removed), OpenSearch worker connection fixed, uploads verified. Superseded for storage by the UGM migration (NFS local files, MinIO retired for app files) |

## Archive (superseded / historical)

No archived plans yet. Superseded execution plans and historical design
documents will be moved to `docs/archive/` when they accumulate.
