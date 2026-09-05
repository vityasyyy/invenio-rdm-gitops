# Backup scheduler deadlock — diagnosis + prepared unblock (2026-09-05)

> Read-only diagnosis. No cluster mutation performed. Every command in §5
> is marked **NEEDS APPROVAL — NOT RUN**. Index update (`docs/plans/README.md`)
> intentionally left to the docs-sync worker.

## Diagnosis

- CNPG `ScheduledBackup/database/postgres-daily-backup` (`0 2 * * *`) is frozen:
  `lastScheduleTime 2026-08-21T02:02:00Z`, `nextScheduleTime 2026-08-21T03:02:00Z`.
- Each reconcile (~every 15 min; observed 07:15/07:32/07:49/08:05 UTC 2026-09-05)
  computes `backupName postgres-daily-backup-20260821030200` from the frozen
  `nextScheduleTime`, attempts creation, and fails:
  `backups.postgresql.cnpg.io "postgres-daily-backup-20260821030200" already exists`
  (`scheduledbackup_controller.go:279`). Status never advances → same name → same
  error, forever.
- The pre-existing object (created 2026-08-21T03:02:15Z, `phase: failed`) carries the
  stale worker-02 streaming-proxy 502. Streaming to worker-02 works again (verified
  read-only 2026-09-05, `logs postgres-3 EXIT:0`), WAL archiving to MinIO works
  (`ContinuousArchiving=True`), Velero BSL `Available`, MinIO pod Running 49d —
  so the deadlock is pure scheduler state, not storage.
- Blast radius: 1872 Backup objects, all `failed`, zero `completed`;
  `LastBackupSucceeded=False`. No restorable base backup exists right now.
- Velero side note: `weekly-infra-backup` Enabled, `lastBackup 2026-08-30`, but
  `get backups` is empty (TTL 672h should retain it) — open question, §6 of
  WORKER-REPORT.md. Kopia maintain jobs healthy (hourly, Complete).

## Fix principle

Delete exactly the one failed blocking Backup CR so the next reconcile creates it
fresh and advances; then prove the path with a uniquely-named manual Backup.

## Prepared commands (ALL NEED APPROVAL — NOT RUN)

```bash
# 1. Audit snapshot (read-only, run first)
kubectl -n database get backup postgres-daily-backup-20260821030200 -o yaml > /tmp/blocking-backup-20260821030200.yaml

# 2. Unblock: delete the failed blocking object ONLY
kubectl -n database delete backup.postgresql.cnpg.io postgres-daily-backup-20260821030200
```

```yaml
# 3. manual-backup-verify-20260905.yaml (kubectl apply -f — NEEDS APPROVAL, NOT RUN)
apiVersion: postgresql.cnpg.io/v1
kind: Backup
metadata:
  name: manual-backup-verify-20260905
  namespace: database
spec:
  cluster:
    name: postgres
  method: barmanObjectStore
```

```bash
# 4. Watch (read-only)
kubectl -n database get backups -w

# 5. Verify manual backup completed
kubectl -n database get backup manual-backup-verify-20260905 -o jsonpath='{.status.phase}{"\n"}'
# expect: completed

# 6. Verify cluster condition flipped
kubectl -n database get clusters.postgresql.cnpg.io postgres -o jsonpath='{range .status.conditions[*]}{.type}={.status} reason={.reason}{"\n"}{end}'
# expect: LastBackupSucceeded=True

# 7. Verify scheduler advanced (allow ~15-20 min after step 2)
kubectl -n database get scheduledbackup postgres-daily-backup -o yaml | grep -A4 '^status:'
# expect: nextScheduleTime NEWER than 2026-08-21T03:02:00Z
```

## Rollback

- Success → nothing to roll back.
- Manual backup 502s again → stop, streaming regressed; re-escalate via
  `docs/plans/active/2026-09-02-proxy-502.md`. Keep the §5-step-2 delete (harmless).
- `already exists` persists → read-only `get backups | grep <name from log>` and escalate.

## Open questions

See WORKER-REPORT.md §9 (hourly historical names vs daily spec; missing Velero
backup objects under 28d TTL; `nextScheduleTime = last + 1h` shape).
