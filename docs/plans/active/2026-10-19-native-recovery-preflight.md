# Native recovery preflight and fresh production backup (#139)

Status: fresh production backup verified; dedicated credential SealedSecret reports
Synced=True without reading its contents. The owner approved deploying two scoped
network allowances and the native restore after verification. No application sample
or native restore has yet been performed. The owner requested execution without repeated
routine confirmations; credentials and material security changes retain their gates.

## Fresh production-scope backup

`recovery-production-20261019` was created from `weekly-infra-backup` after #147.
Completed2026-10-19T10:02:14–10:04:01Z (107s),3681items; no errors/warnings reported.
Expires2026-11-16T10:02:14Z. All six PVB entries Completed:

| Namespace/volume | Entries | Bytes |
|---|---|---|
| invenio/data | 2 web replicas | Empty progress; source volume empty |
| invenio/archive | 2 web replicas | Empty progress; source volume empty |
| monitoring/storage | 1 | 26,060,212 |
| redis/data | 1 | 276,838,281 |

The initial verification expected four PVB entries and failed its count assertion;
inspection established six entries covering exactly four intended volume pairs.
The corrected check verifies every entry Completed and the exact unique coverage.
Do not claim per-replica deduplication from the configuration or a restore of real
application data from an empty source. Redis/Grafana file copies still need their
own consistency/recovery checks. Production-scope backup creation is now successful;
global backup-success metrics remain unable to distinguish required-schedule freshness
from a manual/canary success (see the canary evidence).

## Native destination prepared

Created only `recovery-db-20261019` Namespace (restricted PSA), ResourceQuota and
`recovery-isolation` NetworkPolicy. No CNPG Cluster, PVC or Pod exists there yet.
Quota ceilings:3pods,1PVC/10Gi, requests1CPU/2Gi, limits2CPU/2Gi.

The proposed native Cluster is `postgres-recovery-drill`, one instance, PostgreSQL16.4,
memory request=limit1Gi, CPU250m request/1CPUlimit, new10GiNFS PVC, worker02 affinity
for its existing image cache, no backup schedule, superuser network access disabled,
default transactions read-only. Restore source is external cluster `postgres` and
the existing barman destination `s3://invenio-rdm-backups/postgres`.

Selected native backup: `20261019T090204`, completed09:02:11Z; WAL begin/end
`000000040000000B0000009A`/`000000040000000B0000009B`. `targetImmediate: true`
requires explicit `backupID` on the installed CRD: the first dry run rejected its
absence; the corrected Cluster passed server dry run. This is schema validation,
NOT recovery proof. A later application sample needs a later native recovery point.
Shared NFS free after the production backup:27,001,856KiB (~25.8GiB); recheck before
allocating the recovery PVC. No existing production volume may be rebound.

## Isolation and remaining network gate

Destination egress is limited to CoreDNS53, MinIO9000 and exact Kubernetes API
addresses443/6443. Ingress allows only CNPG operator Pods from `database` on8000.
No public ingress, application workers, scheduler, mail or production PostgreSQL
connections are allowed. The destination policy alone is insufficient:

- Existing MinIO ingress does not admit the new namespace; a scoped rule must match
  namespace `recovery-db-20261019` AND Pod label `cnpg.io/cluster=postgres-recovery-drill`
  on9000 only.
- Existing operator egress admits only production instances in its own namespace;
  allow only this operator selector to that exact recovery namespace/cluster on8000.

The owner approved these two additions through GitOps before native restore.
`security/network-policies/recovery-db-allow.yaml` contains only these exact peers
and ports; the regression check rejects broader specifications. Live convergence
must be verified before creating the native Cluster. Policies remain after the drill
until separately approved cleanup; no automatic namespace/PV deletion is introduced.

## Credential boundary

Owner must provision a new read-only MinIO access key through the approved secret
process, not copy the production root key. Object permission: GetObject/GetObjectVersion
only on `arn:aws:s3:::invenio-rdm-backups/postgres/*`; bucket-level ListBucket and
GetBucketLocation on `invenio-rdm-backups` only. No writes/deletes or other buckets.
Bucket listing exposes names within this backup bucket; object contents outside the
PostgreSQL prefix remain denied.

Required namespaced Secret: `recovery-db-20261019/recovery-minio-readonly`, keys
`ACCESS_KEY_ID` and `ACCESS_SECRET_KEY`. Never send these values in chat. The lead
has not read or copied any secret. The nonsecret policy and draft Cluster manifests
are retained in the approved local session artifact directory.

After the credential and network gates: recheck capacity/headroom, apply only the
recovery Cluster, verify its new PV UID, native recovery logs/read-only SQL and data
checks. App record/file consistency, Redis recovery, independent MinIO/NFS recovery
and representative-scale RPO/RTO remain separate acceptance criteria. No cleanup
or source-backup deletion is authorized by this document.
