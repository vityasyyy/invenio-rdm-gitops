# Recovery drill readiness (#139)

Status: canary executed after explicit approval; file mechanism verified on the
corrected restore attempt. Production sample/native DB recovery remain gated.
Supersedes the historical restore-contract snapshot; #147/#148 are live.

## Current checks

- Worker memory 56%/61%; shared NFS free 27,148,800KiB (~25.9GiB). This is shared
  capacity, not space reserved for a drill; recheck before execution.
- Only Oct18 PartiallyFailed Velero Backup remains, expires Nov15. Do not rely on it
  for full recovery. Do not change its TTL or delete it during this drill.
- CNPG healthy, latest successful native backup Oct19 09:02:11Z. PostgreSQL16.4;
  destination `s3://invenio-rdm-backups/postgres`, in-cluster MinIO endpoint.
- Actual application data/archive mounts still each4KiB, zero files. Empty-volume
  restoration cannot establish recovery of a meaningful application file.

## Version-specific mechanism

Velero1.17.1 FSB restore is Pod/PVR driven. PVC-only metadata restoration is
insufficient. `PodVolumeRestoreAction` prepends the restore-wait init container;
`restore.go` applies resource modifiers AFTER restore actions and before namespace
mapping/resource creation. Replacing all init containers would remove the helper.
Modifier conditions match the original namespace. Never run an unreviewed broad
restore of Invenio/ArgoCD/Velero/database controllers.

Sources:
- https://velero.io/docs/v1.17/file-system-backup/
- https://velero.io/docs/v1.17/restore-resource-modifiers/
- https://github.com/vmware-tanzu/velero/blob/v1.17.1/pkg/restore/restore.go#L1418-L1467
- https://github.com/vmware-tanzu/velero/blob/v1.17.1/pkg/restore/actions/pod_volume_restore_action.go#L114-L213

The running binary confirms namespace mappings, resource-modifier ConfigMap,
resource include filters and `include-cluster-resources` flags. `restore-volumes`
describes snapshot restoration; it is not proof that FSB was triggered.

## Recommended execution sequence (approval required)

1. Validate the mechanism with a standalone canary BEFORE restoring a production
   Pod. Two new namespaces, source and destination; neither runs Invenio or mounts
   production PVCs. One small source PVC, inert sleep Pod using an already cached
   image, no environment credentials, no service-account token or controller.
2. Apply deny-all network policies before creating Pods. Velero performs backup
   through its own agents; no application network access is needed in canary Pods.
3. Create a deterministic harmless <=64KiB file by an explicit exec, NOT a startup
   command. Capture SHA-256/length. The restored Pod must never regenerate the seed.
4. Back up only the canary namespace/Pod and required PVC metadata, no cluster-scoped
   resources; opt-in source data only. Check PVB success, not merely Backup status.
5. Provision a distinct empty destination PVC; verify its PV UID and NFS subdirectory
   differ from source and every production PV. Restore only the inert Pod into the
   destination, preserving Velero's restore helper and binding only destination PVCs.
   Exact resource filters/modifiers must be reviewed offline before submission.
6. Observe PVR success and compare the independently captured checksum/length. Keep
   both namespaces for review; cleanup and PV deletion require a separate decision.

This canary proves the backup/restore mechanism, NOT production record recovery,
database consistency, off-site DR or restoration of Redis/Grafana state. A subsequent
approved application record/file seed and fresh targeted application backup remain
necessary for the complete Batch1 acceptance criteria.

## Native database and application gates

- Native CNPG recovery must use a new Cluster/PVC in an isolated namespace, never
  the production pgdata copy or production PVC. Operator-created Services and required
  API traffic must be included in the isolation review before creating the Cluster.
- Owner must provision drill-only MinIO credentials scoped to reading the existing
  PostgreSQL backup prefix through the approved secret process. Do not read/copy
  production root credentials or paste credentials into chat.
- No application, workers, scheduler, public ingress, PostSync hook or mail sending
  may run in recovery namespaces. Object-store read access is explicitly allowlisted
  for the native recovery Pod; canary namespace remains deny-all.
- Pick a native recovery point after the approved sample record/file exists; document
  file/DB skew rather than claiming application-consistent snapshots from live copies.
- Stop on node pressure, insufficient free capacity, PV UID reuse, unexpected resources
  or external access. No writes to production or existing backups are authorized.

## Approval gate used before execution

Approve canary namespace/PVC/Pod creation, tiny seed creation and one canary Backup/
Restore pair only after the offline manifest/filter review. This is distinct from
approving production sample creation, application backup, native database restore or
cleanup. Retain evidence and report each proof separately.

## Executed canary evidence (2026-10-19 UTC)

The owner approved source/destination namespace creation, a harmless seed, one
Backup/Restore pair, retaining resources. After the first attempt exposed the filter
requirement below, the owner separately approved deleting ONLY the stalled destination
Pod and a corrected second Restore. No production PVC, application record, secret,
service or controller was changed by this drill.

| Item | Evidence |
|---|---|
| Namespaces | `recovery-canary-src-20261019`, `recovery-canary-dst-20261019` |
| PVC in each | `canary-data`, 1Gi RWX, `btd-nfs`; new distinct NFS volume handles |
| Source PV UID | `68e98efe-00ba-4f82-9a17-7f299429ffd7` |
| Destination PV UID | `4670abcc-1a75-4b3d-af6c-b84ca0c57d30` |
| Pod | `canary-reader`: cached Invenio digest, command `exec sleep 86400`; no application startup, credentials or service-account token |
| Seed | 35,840 bytes, created by explicit exec, not by Pod startup |
| SHA-256 | `d47cb31fa4d9a9e103a17867aba7c0b5ec1c328fbc237f95e05f5cf3d2af37df` |
| Backup | `recovery-canary-20261019`, Completed, 09:45:52–09:46:06 (14s), no errors/warnings reported |
| PVB | `recovery-canary-20261019-vj78z`, Completed, 35,840 bytes |
| Corrected Restore | `recovery-canary-20261019-v2`, Completed, 09:50:08–09:50:22 (14s), zero errors, two explained warnings |
| PVR | One Completed PVR for v2; 35,840 bytes, 09:50:15–09:50:21 |
| Integrity | Restored SHA-256/length equal source; uid1654 retained; verifier mount read-only |
| Isolation | Deny-all ingress/egress in both namespaces; API TCP connection timed out from source and destination; no public ingress or app processes |
| Production checks | All17 Argo Apps Synced/Healthy, ping200; every captured pre-drill PV UID retained |
| Node memory afterward | Worker01 55%, worker02 63%; these are observations, not load-test certification |

### First attempt: Completed did not mean data restored

Restore `recovery-canary-20261019` included only `pods`. It reported Completed
at09:46:21, but created no PVR; its `restore-wait` init container waited indefinitely.
Velero1.17.1 `pkg/restore/restore.go:1727–1731` explicitly requires BOTH PVC and PV
types to pass the resource-type filter before calling `restorePodVolumeBackups`.
The earlier source inspection identified Pod/helper mechanics but missed this
additional condition. This is why the initial restore is NOT counted as a success.

The corrected Restore uses:

```yaml
includedResources: [pods, persistentvolumeclaims, persistentvolumes]
includeClusterResources: false
namespaceMapping:
  recovery-canary-src-20261019: recovery-canary-dst-20261019
existingResourcePolicy: none
```

Including the PV TYPE satisfies the PVR guard; `includeClusterResources: false`
still prohibits actual cluster-scoped PV restoration (`restore.go:1147–1151`).
Both claims were pre-provisioned before either attempt, and the destination binding
was verified unchanged. Resource modifiers applied only to the source canary Pod:
make its main volume mount read-only and harden the already-added restore helper
without removing it. No production resource matched the modifier conditions.

The two v2 warnings were reviewed, not ignored:
- Source PV metadata absent from the namespace-only backup: the PVC action requested
  it as an additional item, but it was not present/restored.
- Destination PVC already existed and differed from the source: policy `none` kept
  its fresh binding; source and destination PV UIDs remained distinct and unchanged.

### Retained resources and limits

Both namespaces, both1Gi PVCs, source/restored Pods, successful canary Backup, first
and corrected Restore records, modifier ConfigMap and snapshot evidence remain.
Pods sleep for86400s; cleanup needs separate approval. The canary Backup has28-day
TTL and expires2026-11-16T09:45:52Z; it is not an indefinite archive. The existing
Oct18 PartiallyFailed production Backup remains, expiresNov15.

The canary created a tenth Ready BackupRepository, reaching the ten-repository
assumption in the tested Velero budget. Another namespace/repository backup needs
a budget review first; do not silently extend the drill with new source namespaces.

The canary also exposes a monitoring limitation: after its success, the unlabelled
global `velero_backup_last_successful_timestamp` advanced to1792403166 and the stale/
never-success/missing backup alerts were absent. The production Oct18 Backup is still
PartiallyFailed. A successful canary therefore masks production-schedule freshness
when the rule uses this global gauge. Before baseline acceptance, monitoring must
track the required schedule independently (and test manual/drill success alongside
stale/failed production backups). No alert was silenced or rule changed in this drill.

These tiny-file timings are NOT an application-scale RTO/RPO. Still unproven:
native CNPG recovery, application record/file consistency, Redis/Grafana recovery,
independent NFS/MinIO disaster recovery and performance at representative volume size.
Next gate: owner-provisioned drill-only read credentials for the PostgreSQL prefix,
plus separate approval for an application seed, fresh application backup and native
isolated DB recovery. No production credentials were read or copied.
