# WORKER-REPORT — CNPG Postgres Recovery (issue #76, branch fix/74-db-recovery)

**Date:** 2026-09-02 · **Role:** investigator + preparer (read-only; no mutations performed)
**Issue:** #76 (T3) — Recover CNPG postgres cluster to declared single-instance state
**Note:** branch slug references #74, but the actual issue is **#76** (the #74 issue is the worker-02 rebalance, which itself references "the crash-looping CNPG replicas (fixed by #74)" — circular; #76 is the authoritative issue for this work).

## What was found (diagnosis evidence)

1. **Drift confirmed:** `spec.instances: 1` (declared in git since #53, commit 1558167) but `status.instances: 3`. `postgres-3` is the sole healthy primary (Ready=True, 1 restart, running since 2026-08-14). `postgres-1` (445 restarts) and `postgres-2` (368 restarts) crash-loop for 18 days.
2. **WAL archiver deadlock confirmed (postgres-2 logs):** every ~10 min reconcile: `barman-cloud-check-wal-archive: ERROR: WAL archive check failed for server postgres: Expected empty archive` with 11 ready WALs pending. The non-empty `wal/` prefix in `s3://invenio-rdm-backups/postgres` blocks the check → instance never ready → operator never completes scale-down.
3. **postgres-1 failure mode:** startup probe 500 (`HTTP probe failed with statuscode: 500`); container exits `Completed` (exit 0) after ~60 min, kubelet restarts. Root cause not fully diagnosed (logs unreadable via 502 proxy) — likely the same archiver/status-extraction path; removal is the fix regardless.
4. **Cluster conditions:** `Ready=False (ClusterIsNotReady)`, `ContinuousArchiving=False (ContinuousArchivingFailing)`, `LastBackupSucceeded=False (LastBackupFailed)`.
5. **Backups:** 1,871 Backup CRs, **0 completed, all failed** since 2026-06-04 (90d) with `error dialing backend: proxy error from 127.0.0.1:9345 while dialing 10.17.117.43:10250, code 502` — the kubelet streaming-proxy issue tracked as **#75**. ScheduledBackup `postgres-daily-backup` stuck at `nextScheduleTime: 2026-08-21T03:02:00Z` (not advancing since 2026-08-21).
6. **MinIO bucket contents NOT verified:** `kubectl exec` into MinIO failed twice with the same 502 proxy error (MinIO pod is on worker-02, 10.17.117.43). Per escalation rule, noted and not retried. Expected structure (barman): `wal/` (stale WALs — the blocker), `base/`, `backups/`. **Lead must run Step 1a (`mc ls`) before moving anything.**
7. **No manifest change needed:** `k8s/apps/invenio-deps/postgresql/cluster.yaml` already declares `instances: 1`; drift is purely in-cluster. This wave is docs-only.
8. **Other:** PVCs postgres-1/2/3 all Bound (10Gi, btd-nfs); PDB `postgres-primary` (minAvailable 1) won't block replica removal; netpols allow postgres→MinIO:9000; operator is Helm `cloudnative-pg-0.23.0` (image 1.25.0), 41 restarts, on worker-02.

## Prepared commands (lead executes with user approval — NOT run)

Full detail in `docs/plans/active/2026-09-02-db-recovery.md` → "Execution Steps". Summary:

1. **Pre-flight (read-only):** verify postgres-3 Ready=True, restart count unchanged; verify spec/status instances = 1/3.
2. **Move-aside (reversible, REQUIRES APPROVAL):** `mc mv --recursive local/invenio-rdm-backups/postgres/wal/ local/invenio-rdm-backups/postgres-stale-2026-09-02/` (via `kubectl exec -n minio deploy/minio`). Count objects before/after. Rollback = `mc mv` back. If exec 502s, run `mc` from a worker-01 pod or wait for #75.
3. **Scale-down (REQUIRES APPROVAL):** no patch needed — spec already says 1. Watch `kubectl get pods -n database -w`; if postgres-2 still deadlocks, `kubectl rollout restart deploy/invenio-postgresql-cloudnative-pg -n database` to force reconcile.
4. **Force-removal (LAST RESORT, REQUIRES EXPLICIT USER APPROVAL):** only if operator won't scale down: `kubectl delete pod postgres-1 postgres-2 -n database --grace-period=0 --force` (replicas only; PVCs retained; WALs preserved).
5. **Manual backup (REQUIRES APPROVAL):** `kubectl create -f - <<EOF` Backup CR `manual-recovery-verify-20260902` (cluster: postgres, method: barmanObjectStore); watch `phase=completed`.
6. **Verify:** cluster `1/1/1`, Ready=True, ContinuousArchiving=True, LastBackupSucceeded=True, scheduled backup advances.

## What the lead must approve/execute

- [ ] Step 1 move-aside (reversible, but mutates MinIO)
- [ ] Step 2 operator reconcile/restart
- [ ] Step 3 force-removal of postgres-1/2 (only if Step 2 stalls; explicit approval required)
- [ ] Step 4 manual Backup CR
- [ ] Confirm bucket contents with `mc ls` before any move (Step 1a)

## Escalations / open items

- **#75 (kubelet 502 proxy):** blocks `kubectl logs/exec` on worker-02 pods (operator, postgres-1, MinIO). Backup pipeline will keep failing until fixed — manual backup in Step 4 may fail with 502; if so, document and escalate, do not retry in a loop.
- **postgres-1 root cause** (startup probe 500) not fully diagnosed — logs unreadable via 502. Removal is the fix; no further investigation needed for this wave.
- **Restore drill** deliberately NOT in this wave (see plan "Restore drill (follow-up)" section) — needs healthy cluster + completed backup + #75 resolution first.

## Deliverables in this worktree

- `docs/plans/active/2026-09-02-db-recovery.md` (new — plan + evidence + prepared commands)
- `docs/plans/README.md` (index updated)
- `WORKER-REPORT.md` (this file)
- Commit: `docs: db-recovery plan with prepared execution steps (#76)` — docs-only, no cluster mutations
