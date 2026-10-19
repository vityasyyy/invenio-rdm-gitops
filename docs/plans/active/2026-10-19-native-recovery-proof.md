# Native PostgreSQL recovery proof (#139)

Status: native recovery of existing data verified; full application recovery and
experiment readiness are NOT certified. Source/canary/drill resources remain intact.

## Authorization and isolation

The owner provisioned the drill-only credential and explicitly approved deploying
the two scoped network policies, native recovery and replacing only the failed scratch
Job after correcting MinIO permissions. No production root credential was read/copied.
PR#151 (`e6e015c`) passed27Python tests and all CI. DeployVerify39238596934 passed;
lead additionally verified the exact live policy specs and deployed revision before
creating the native Cluster. No production PostgreSQL ingress was added.

- Cluster: `recovery-db-20261019/postgres-recovery-drill`, PG16.4, one instance.
- Source native backupID: `20261019T090204`, completed09:02:11UTC, targetImmediate.
- New PVC: `postgres-recovery-drill-1`, UID`e804d999-2378-4bdb-a5da-3b0daecee99b`.
- New PV UID: `dfebe444-7fad-43b5-8a11-17fdcf1c4fec`, distinct NFS subdirectory;
  binding unchanged throughout the attempts, no source volume reused.
- Memory request=limit1Gi, CPU250m/1CPU, worker02 affinity, namespace2Gi ceiling.
- Clone egress only DNS, API and MinIO; production PostgreSQL TCP connection test
  timed out. No application, Celery, public ingress or mail process was deployed.
- No `spec.backup`/ScheduledBackup configured for the clone. Source object-store
  credentials are owner-provisioned read-only; no source write/delete was attempted.

## Failures and bounded corrections

1. Initial operator Job at10:36:53 failed `HeadBucket`403 before restoring data.
   Its bounded retries ended at10:48:02. Network reached MinIO; permissions/credentials
   were the blocker. Owner confirmed policy correction and approved scratch Job retry.
2. Deleting the failed Job did not make CNPG1.25 recreate it: the operator waited
   on the PVC's `initializing` annotation. A replacement Job was reconstructed from
   matching `v1.25.0/pkg/specs/jobs.go`, containers, volumes and environment templates,
   referencing the same Cluster/SA/PVC. Backoff0 and deadline300s prevent retry storms.
3. Download and WAL recovery then succeeded, but the preparation's clone-only
   `default_transaction_read_only=on` blocked internal `CREATE ROLE` (SQLSTATE25006).
   This was a drill configuration defect, not a damaged production backup.
4. Removed that clone parameter; bootstrap must perform internal catalog writes.
   Preserved the partial directory as `pgdata.failed-readonly-20261019T1055` rather
   than wiping it. Retried the bounded scratch Job on the same PVC; SQL verification
   uses explicit read-only transactions, not a global bootstrap prohibition.

## Verified result

Final Job11:01:04–11:02:08UTC Completed; ClusterReady11:02:30UTC. Final successful
path86s including primary readiness, but initial creation to readiness was25m37s,
including credential and orchestration corrections. Do NOT present64s as end-to-end
RTO or extrapolate these measurements to a representative application workload.

Read-only SQL checks against clone and production:

| Check | Result |
|---|---|
| Schema fingerprint | Both `b6ee5d10de3e7659e4e0c5f64204240a` |
| Users | Both6; no credential/user contents printed |
| Vocabularies | Both9,138 |
| Vocabulary JSON content digest | Both `cd8ba31a512dbbfe63a27074422915b7` |
| Records/drafts/files | Both0 in checked tables; no application sample exists |
| WAL receiver | Clone0; recovered/promoted, not streaming from production |
| Verification transaction | Read-only; `pg_is_in_recovery=false` |

The counts/digests matched the later live source at verification time. This does not
prove that every table is byte-identical or that an arbitrary later recovery point
contains future writes. The source backup's time is the selected recovery point.

Afterward: all17ArgoAppsSynced/Healthy; production database healthy, ping200;
worker memory54%/65%, NFS26,685,440KiB free. Every captured production/canary PV UID
remained. Partial directory175,488KiB and active pgdata183,336KiB both retained.

## Remaining work

- Seeded Invenio record/file recovery and file/DB consistency are still unproven:
  current record/file tables and application file volumes are empty.
- Required-schedule freshness must not be masked by manual/canary successes.
- Redis/Grafana recovery, off-site backup-store protection, sealed-secrets-key/host
  recovery and representative-scale RPO/RTO remain separate limitations.
- Clone, PVC, partial copy, canary, backups and scoped policies remain for review.
  Cleanup needs its own approved scope; do not delete source data or retained backups.
