# Current verified cluster state

Verified **2026-10-25 around 09:45 UTC** against `main` at `d7ef120`. This is a
dated operational snapshot, not a permanent guarantee. Re-run the checks at the end
before relying on it for an experiment or an incident decision.

## Summary

- Three Ready RKE2 nodes; Kubernetes `v1.32.6+rke2r1`, Ubuntu 24.04 LTS.
- **18/18 ArgoCD Applications Synced and Healthy** at the recorded revision.
- Invenio public `/ping` returned **HTTP 200**.
- The GitOps, observability and setup fixes are deployed; no database migration was
  required.
- **The cluster is operationally stable but the formal experiment is not ready.**
  The readiness table below lists the remaining gates.

## Machines and current placement

| Node | Role | CPU / memory sample |
|---|---|---|
| `ubuntu-btd-kubernetes-server` | control-plane, etcd | 7% / 39% |
| `ubuntu-btd-kubernetes-worker-01` | worker | 11% / 59% |
| `ubuntu-btd-kubernetes-worker-02` | worker | 4% / 59% |

`kubectl top` is a single sample. The proposal's sustained per-node memory threshold
is below 70%; this sample passes, but no sustained window or two-environment capacity
proof exists.

| Workload | Current placement |
|---|---|
| Invenio web (2 replicas, HPA 2–4) | worker-01 and worker-02 |
| Invenio workers (3 replicas, HPA 1–4) | worker-01 (2) and worker-02 (1) |
| Invenio scheduler (1 replica) | worker-01 |
| PostgreSQL (1 CNPG instance) | worker-02 |
| Redis master | worker-01 |
| OpenSearch (single node) | worker-02 |
| MinIO | worker-01 |
| Loki / Grafana | worker-02 |
| Velero node-agent | worker-01 and worker-02 |

Placement is chosen by the Kubernetes scheduler from requests and constraints; it is
not a fixed assignment. See
[scheduling and placement](architecture-guide.md#scheduling-and-placement).

## Scheduling and orchestration

- HPA changes the **number of replicas**. The Deployment/StatefulSet controller creates
  Pods; the Kubernetes scheduler binds each Pod to a node.
- The scheduler uses CPU/memory requests, node allocatable capacity, taints and
  tolerations, node selectors/affinity, pod affinity/anti-affinity, topology spread,
  and volume locality. It is automatic; we declare constraints and requests, not a
  fixed node plan.
- Invenio web, worker and scheduler templates have **no nodeSelector or node affinity**
  and use `ReadWriteMany` NFS PVCs, so they can be placed on either worker. Current
  placement reflects scheduler decisions at this snapshot.
- PostgreSQL is a **single CNPG instance** with preferred pod anti-affinity. With one
  instance there is no database failover; if its node or Pod is lost, the instance must
  be recreated and its PV reattached.
- Alloy deliberately excludes control-plane nodes. Cloudflared is a DaemonSet that
  tolerates control-plane taints. Velero node-agent is a DaemonSet on the workers.
- Velero `PodVolumeBackup` expose Pods are created on the **same node as the source
  Pod** because they need that node's `/var/lib/kubelet/pods` hostPath. "worker-01" or
  "worker-02" in a backup report is source-Pod locality, not a fixed Velero assignment.
- The 25 October recovery drill explicitly pinned its one instance to worker-02 with
  node affinity. That was a drill constraint; production Invenio does not have it.

## Storage and files

- Invenio's live default file location is `s3://invenio-rdm/files/`. The image
  includes `invenio-s3`/`s3fs`; the app uses
  `invenio_s3.s3fs_storage_factory` with endpoint `https://minio.vityasy.me`.
- A private seed draft exists: `4ben9-bgh38`, file `seed.bin`, **65,536 bytes**,
  MD5 `8f1445bafe2c2095044af7789462f475`, SHA-256
  `7daca2095d0438260fa849183dfc67faa459fdf4936e1bc91eec6b281b27e4c2`.
  The public draft-file download returns HTTP 200 with the matching SHA-256.
- Only `/invenio-rdm/` is routed at `minio.vityasy.me`; other buckets remain
  unrouted and unauthenticated requests receive a 403. MinIO credentials remain in
  the SealedSecret.
- `invenio-data` and `invenio-archive` are mounted but remain empty; they are not
  the authoritative file store and are not opted in to Velero.
- A daily `invenio-orphan-cleanup` CronJob (04:15) removes S3 objects with no
  `files_object` reference and then clears orphan `FileInstance` rows. The packaged
  job was exercised through a one-off Job: it removed one reset orphan and preserved
  the seed object.
- MinIO `/export` held about **35 GiB** at the earlier snapshot (~26 GiB free, 63%
  used): approximately 33 GiB PostgreSQL backup data, 2.1 GiB Velero data, and the
  application bucket. The MinIO PVC requests 20 GiB, but NFS does not enforce that
  claim.
- MinIO is both the primary file store and the backup store on the same NFS server.
  This is a **single failure domain**; no independent off-site copy exists.
- Shared NFS also carries PostgreSQL, Redis, OpenSearch, Loki, Prometheus, Grafana and
  Alertmanager volumes. PVC sizes are requests, not per-directory quotas.

## Backups and recovery

| Item | Current state |
|---|---|
| CNPG schedule | `0 0 2 * * *` — daily at 02:00 UTC; next run 2026-10-26 02:00 UTC |
| CNPG retention | 30 days; **not reduced to 7 days** |
| Velero schedule | `0 3 * * 0` — weekly Sunday 03:00 UTC; TTL 672h (28 days) |
| Next Velero run | 2026-11-01 03:00 UTC |
| 25 October Velero run | `PartiallyFailed`, one error; duplicate per-replica volume opt-ins were removed afterward |
| Velero alert | `VeleroScheduledBackupFailed` is firing for the 25 October run until a successful weekly run supersedes it |

The 25 October run created three `data` and three `archive` PodVolumeBackups. Two
duplicate `archive` backups on worker-01 were canceled after their expose Pods failed;
the same shared PVC completed on worker-02. The run then waited the four-hour item
timeout and finished partial. One Completed copy exists per opted-in PVC, but the
expose-Pod cause is not proven. The next weekly run is the evidence gate for the
deduplication fix.

### Verified recovery drill

| Check | Production | Drill |
|---|---|---|
| Source backup | — | `20261025T080202`, completed 08:02:12 UTC |
| Public tables | 80 | 80 |
| Schema digest | `b6ee5d10de3e7659e4e0c5f64204240a` | same |
| Users / roles | 7 / 1 | 7 / 1 |
| Vocabularies | 9,138 | 9,138 |
| Vocabulary digest | `5fa4a963305262df6c7ad09cb88887e3` | same |
| Records / drafts / files | 0 / 1 / 1 | 0 / 0 / 0 (pre-seed drill) |
| File locations | 1 | 1 |
| `pg_is_in_recovery` | false | false |

The drill Cluster was created at 09:39:43 UTC and Ready at 09:41:28 UTC (105 seconds),
on a new 10 GiB PVC/PV on worker-02. Production stayed healthy: 18/18 Argo
Applications Synced/Healthy, PostgreSQL healthy, `/ping` 200.

This proves **database recovery from the current native backup chain**. The drill
predates the private seed, so it does not prove that the seed file is inside a
database-consistent backup. The seed upload/download path is now proven separately;
recovery of a seeded record/file remains a follow-up gate. The drill Cluster,
namespace, Secret and scoped network policies were removed. One Released `Retain`
10 GiB PV remains as evidence; physical NFS cleanup is a separate storage action.
The read-only MinIO user/key created for the drill must be revoked at MinIO.

## Setup, observability and current alerts

- The Invenio non-secret configuration is a shared Kustomize `Component`
  `k8s/apps/invenio/config/` with `configMapGenerator`. A config change produces a
  new `invenio-app-config-<hash>` name; all `envFrom` refs change and ArgoCD rolls the
  workloads. The 2026-10-26 deployment verified this with no manual restart.
- The Invenio `PostSync` hook is **read-only** and verifies Alembic dependency-resolved
  heads, default location, index aliases, queues and admin permission. It fails closed.
  Every Argo hook Job now has a positive `activeDeadlineSeconds` and
  `BeforeHookCreation,HookSucceeded,HookFailed` cleanup.
- Migrations, fixtures, queue declaration and reindexing remain a manual initialization
  Job outside the automated Kustomization; this is a deliberate one-off exception.
- Grafana can reach Loki and Alertmanager. The `invenio-logs` dashboard is provisioned.
- `VeleroScheduledBackupFailed` remains active for the 25 October run. The MinIO
  `PodNotReady` alert cleared after the bucket hook repair.
- `CPUThrottlingHigh` was **info** for the scheduler container and is not routed to
  Discord by the current severity routes.

## GitOps rollout contract and imperative exceptions

- All 18 Argo Applications use automated sync and self-heal. `argocd-self` and
  `sealed-secrets` intentionally have `prune: false`; their resources are still
  Git-declared.
- Routine configuration and workload changes are Git-only. The shared config
  component produces a new hash and Argo rolls the pods; do **not** use
  `kubectl rollout restart` or manually apply a workload.
- The historical `kubectl.kubernetes.io/restartedAt` annotations left by the
  file-storage repair were removed on 2026-10-26. No live-only rollout annotation
  remains.
- Deliberate imperative exceptions:
  - first cluster/ArgoCD bootstrap (`scripts/bootstrap-infra.sh`);
  - one-off full initialization and migrations (`k8s/apps/invenio/initialization/`);
  - secret creation/rotation/revocation, which must never enter Git;
  - data operations such as seed/reset and restore;
  - break-glass recovery of a stuck Argo operation or hook.
  These are versioned, documented and bounded; they are not routine sync steps.
- The daily orphan cleanup CronJob is the declarative form of a data-maintenance
  action. It is not a hook and does not block application syncs.

## Experiment readiness

**No, the formal experiment is not ready.** Operational stability is not experiment
readiness.

| Gate | Status | Evidence / blocker |
|---|---|---|
| Real Invenio workload exists | Partial | Production Invenio is healthy with one private draft and one 64 KiB file; no published record |
| Private draft + 64 KiB file seed | Complete | `4ben9-bgh38`; upload and public download verified, SHA-256 matches |
| Reset/orphan cleanup | Complete | Draft deletion + packaged CronJob removed the test file instance and S3 object; seed preserved |
| A/B real-Invenio environments | Blocked | Only the `invenio` namespace exists; no manual/GitOps twin provisioning |
| HPA-control method review | Blocked | Research PR #6 is open; scenario A method needs owner/supervisor review |
| Live experiment instruments | Blocked | Current scripts are offline evidence validators; no approved live collection/reset adapter |
| Kubernetes audit evidence | Blocked | No API-server audit evidence established; Git is not an audit of out-of-band changes |
| SMTP/email | Blocked | Live config still uses `smtp.example.org` and `admin@example.org`; relay/auth/CA/firewall unknown |
| Capacity | Unproven | One sample is 59% memory on both workers; no sustained window or A/B placement proof |
| Database recovery | Proven | 25 October native drill matched schema, counts and vocabulary digest |
| File/record consistency | Partial | One private seed file is uploaded/downloaded; no published record and no independent file copy |
| Weekly backup freshness | Blocked | 25 October run `PartiallyFailed`; next successful run is 1 November |
| Independent disaster recovery | Blocked | MinIO primary and backup data share one NFS failure domain |
| Formal run approval | Blocked | No unmeasured pilot or formal collection approval |

The seed and reset path is complete. The next useful steps are: provision the two
real-Invenio environments; review and freeze the method; then run a bounded
unmeasured pilot. Recovery of a seeded record/file still needs a post-seed drill.

## Next evidence gates

1. **2026-10-26 02:00 UTC** — verify the CNPG daily run, not an hourly run.
2. **2026-11-01 03:00 UTC** — verify the weekly Velero run has no duplicate PVBs and
   the `VeleroScheduledBackupFailed` alert clears.
3. Provision real-Invenio A/B environments and a live, reviewed measurement adapter.
4. Run a post-seed isolated recovery drill (database + seed file).
5. Obtain audit/SMTP/capacity/NTP/maintenance evidence from university IT.
6. Add an independent copy of the MinIO file/backup data.
7. Revoke the drill MinIO key and decide on the retained drill PV/NFS directory.

## Recheck commands

```bash
kubectl get nodes -o wide
kubectl -n argocd get applications
kubectl -n invenio get hpa,pods -o wide
kubectl -n invenio get cronjob invenio-orphan-cleanup
kubectl -n invenio get configmap | grep invenio-app-config
kubectl -n database get clusters.postgresql.cnpg.io,scheduledbackups.postgresql.cnpg.io
kubectl -n database get backups.postgresql.cnpg.io --sort-by=.metadata.creationTimestamp
kubectl -n velero get schedules,backups
kubectl -n invenio exec deploy/invenio-web -c web -- invenio files location list
```
