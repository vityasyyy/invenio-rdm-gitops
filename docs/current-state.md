# Current verified cluster state

Latest scoped verification: **2026-10-29 around 04:23–04:30 UTC**, including the
six-hour post-rollout observation below. The broader baseline sections retain their
**2026-10-25 around 09:45 UTC** observation against `d7ef120`; they are not all
re-certified by this scoped rollout. Re-run the relevant checks before relying
on a dated observation for an experiment or incident decision.

## Post-rollout background window — 2026-10-29

Fixed summary window **2026-10-28 22:23:34 to 2026-10-29 04:23:34 UTC**,
15-second evaluations. Both surviving worker pods have 1,440 CPU/memory samples;
the full retained query window starts at 18:30 UTC. This is observational
background evidence, not a controlled load test or formal experiment.

- Worker CPU means **8.45/8.63m per pod**, compared with approximately
  **141–142m** in the reconstructed historical window. Readiness probe means
  **0.764/0.781s**, with 180 executions per pod; liveness **0.757/0.776s**,
  with 72 per pod. No duration-counter reset or failed-probe series was observed.
- Worker working-set medians **351.17/350.65MiB**, sampled peaks
  **367.59/373.66MiB**. Shared-image restart, different pod populations and
  workload differences prevent attributing all memory change to the helper.
- Web and worker HPA current/desired stayed at **2** throughout this window.
  All five application pods had zero restarts; worker OOM-event counters and
  reported node Memory/Disk/PIDPressure stayed zero. All 18 Applications were
  Synced/Healthy at the live check. MinIO retained a historical terminal Error
  operation; it is not evidence of a new failed deployment.
- Raw metrics-server working-set/allocatable sample at approximately 04:23 UTC:
  server **45.98%**, worker-01 **57.92%**, worker-02 **59.71%**. Historical node
  working-set series were unavailable in the queried Prometheus metrics. Host
  `1-MemAvailable/MemTotal` history is retained separately and must not substitute
  for the experiment gate. Sustained compliance with that exact gate is unproven.
- Read-only Redis: `vocabularies` backlog **264,140**, `celery` depth **0**,
  evictions/rejected connections **0**, AOF enabled with last write/rewrite OK.
  Available worker logs contain no entries in the requested interval; task/queue
  metrics are absent. This does **not** prove task progress or an empty workload.
- Alloy generic-drop counters did not increase and canary missing counters stayed
  zero. Cold-start replay remains unresolved. Both **VeleroScheduledBackupFailed**
  and **VeleroBackupStale** fired throughout; neither was suppressed.

Local exact queries/responses, summary, snapshots and aggregate checks are retained
with SHA-256 manifests in
`~/skripsi/experiments/evidence-local/post-probe-acceptance-20261029T042334Z/`.
This is not an off-site backup. See [worker findings](worker-probe-findings.md).

## Worker probe rollout — 2026-10-28

- Helper packaging #196, image delivery #198 and probe activation #197 are
  merged. The shared pinned image is
  `sha256:52645b7b3804fca0178d4428994372356669569dd31b560f096615fdf343a357`.
- Image build/verification `40737568055`, image-delivery Deploy Verify
  `40742121674` and activation Deploy Verify `40744166618` passed.
- Invenio was Synced/Healthy with operation Succeeded at `3c471a3`; the three
  new worker pods were Ready with zero restarts and helper-based probes.
- Successful readiness probes averaged **0.76–0.82 s** over the first few
  samples, versus approximately **12–14 s** before. The installed helper also
  returned exit 0 in 1.013 s. Long-term CPU/memory and replica savings remain
  unverified.
- Public `/ping` passed through the deployment smoke test and native curl. The
  existing harness verified private seed `4ben9-bgh38`: 65,536 bytes and SHA-256
  `7daca2095d0438260fa849183dfc67faa459fdf4936e1bc91eec6b281b27e4c2`.
- A post-rollout sample showed web 2/2, worker 3/3 and scheduler 1/1; node memory
  47/60/55%. These fresh-pod measurements are **not sustained capacity proof**
  and must not be attributed solely to the probe correction.
- HPA policy, resources, concurrency, node capacity and the 70% experiment gate
  were not changed. The incomplete batch remains stopped; instrument/method
  defects and a warm baseline still block another formal collection. R3 is not
  measured; off-site copy remains deferred.

See [worker probe findings](worker-probe-findings.md) for the failure history,
serializer contract, evidence limitations and rollback procedure.

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
| CNPG schedule | `0 0 2 * * *` — daily at 02:00 UTC; 26–29 October daily runs completed, latest 29 October 02:00:15–02:00:21 UTC |
| CNPG retention | 30 days; **not reduced to 7 days** |
| Velero schedule | `0 3 * * 0` — weekly Sunday 03:00 UTC; TTL 672h (28 days) |
| Next Velero run | 2026-11-01 03:00 UTC |
| 25 October Velero run | `PartiallyFailed`, one error; duplicate per-replica volume opt-ins were removed afterward |
| Velero alerts | Scheduled-failure critical and stale-backup warning observed 29 October; actual successful weekly execution remains unproven |
| Operational S3 file backup | #204 (`13eb258`) deployed the suspended `invenio-rdm-file-backup` CronJob and rules on 29 October. Zero backup Jobs; destination bucket, dedicated Secret, activation, retained points and isolated recovery proof remain pending |

### Suspended file-backup deployment — 29 October 10:04–10:07 UTC

The owner approved suspended deployment only. All six PR checks in run
`40849767229` and post-merge Deploy Verify `40854677445` passed, including the
revision/health/operation/hook checks and `/ping` smoke test. Both affected Argo
applications reached `13eb258` with successful operations. The CronJob was
suspended with zero owned/active backup Jobs; all three backup rules evaluated
healthy/inactive with no errors at 10:06:40 UTC.

This is configuration deployment, not successful backup execution. Actual image/
placement/resource behavior and metric contracts, private provisioning, first
backup, isolated seed/staging/native-DB recovery and activation remain gates in
#202/#139. No prune release or experiment collection was authorized.

### Weekly Velero evidence

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
- ArgoCD metrics are scraped from `argocd-metrics:8082` through a ServiceMonitor and a
  least-privilege network policy. Prometheus has `argocd_app_info` for all 18
  Applications. The `argocd` alert group covers non-Synced, unhealthy,
  stuck-operation, and missing-scrape states.
- Grafana has six dashboards: overview, Invenio app, data/backups, platform,
  application logs, and **05 GitOps & Reconciliation**. The last is R1-style
  consistency evidence and explicitly not R2/R3 measurement.
- Verified component versions: Kubernetes `v1.32.6+rke2r1` on Ubuntu 24.04 LTS,
  ArgoCD `v2.12.0`, OpenSearch `2.19.1`, MinIO
  `RELEASE.2024-12-18T13-15-44Z`. CNPG operator version is not asserted here.
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
| Sequential real-Invenio arms | Selected, not yet validated end-to-end | One real production-like stack, automated imperative comparator and GitOps sequentially; no twin stack or NGINX surrogate |
| HPA-control method review | Aligned | Proposal PR #7 uses HPA-owned scenario A and excludes `/spec/replicas` from static R1 |
| Live experiment instruments | Blocked on corrections | Pilot and stopped partial collection exist; retry-ID validation, baseline waiting, exact node accounting, timing and R1 declaration provenance need correction/validation |
| Kubernetes audit / R3 | Not measured | No usable API-server audit stream established; R3 is excluded, not fabricated from Git history |
| SMTP/email | Operational follow-up | Placeholder relay remains; IT relay/auth/CA/firewall details needed. Not automatically an R1/R2 gate unless the selected protocol uses email |
| Capacity | Partial evidence | Six-hour background history and exact current node sample retained; no exact historical gate series or loaded recovery headroom proof; keep 70% unchanged |
| Database recovery | Proven | 25 October native drill matched schema, counts and vocabulary digest |
| File/record consistency | Partial | One private seed file is uploaded/downloaded; no published record and no independent file copy |
| Weekly backup freshness | Blocked | 25 October run `PartiallyFailed`; next successful run is 1 November |
| Independent disaster recovery | Deferred limitation | MinIO primary and backup data share one NFS failure domain; off-site copy explicitly deferred |
| Formal run approval | Pending | Previous attempts are incomplete; no new formal batch approved after apparatus corrections |

The selected direction is sequential automated-imperative and GitOps arms on one
real stack. The seed/reset path is verified separately from seeded recovery.
Earlier pilot and partial attempts do not validate a fresh complete comparison.
Instrument corrections, method/supervisor alignment, capacity and post-seed
recovery readiness remain gates; R3 and off-site copy are disclosed limitations,
not silently revived requirements or completed measurements.

## Experiment harness status

- The real-Invenio sequential-arm harness is merged in the thesis repository at
  `experiments/live/`. The pre-correction baseline on 29 October passed 127
  offline tests; passing units did not catch the reproduced integration defects.
- Stopped `formal-20261027c`: 210 planned cells, 60 observed, 9 right-censored,
  1 baseline-failed and 140 pending; 76 attempt rows. GitOps never started.
  Earlier partial attempts, pilot and synthetic data remain separate. None is
  a completed formal comparative dataset or a source of replacement samples.
- Dry-run scenarios emit planned mutations with blank timings/R1 and perform no
  `kubectl`/HTTP calls.
- Live mode requires `--live`, `--confirm-single-stack`, an explicit
  namespace/app, `INVENIO_TOKEN`, a frozen source revision, and a render digest.
- The imperative arm pauses and restores both `apps` and `invenio-bootstrap` in
  `finally`; quarantine cleanup is ownership-checked.
- A 2026-10-27 pilot used the existing production-like `invenio` stack, not an
  `exp-*` twin. It is **pilot evidence, not formal data**.
- GitOps pilot results: A observed (HPA detection 1.29s/recovery 19.32s); B
  observed (ArgoCD 1.19s/17.23s); C observed (ArgoCD 1.28s/65.71s); D/E observed
  with detection right-censored by the 2s sampler; F right-censored with cleanup
  OK; G right-censored and not an ArgoCD self-heal case (its annotation is removed
  by the harness, not Argo).
- Imperative A observed after the harness fixes (HPA detection 1.30s/recovery
  23.39s; arm state restored; web 2/2). No seed/record data was changed.
- The initial live attempts exposed and fixed real harness issues: Cloudflare
  rejected the default Python User-Agent; the content endpoint rejects
  `application/octet-stream`; Python forwarded API headers to the presigned MinIO
  redirect; the sampler shadowed `Thread._stop`; and scenario G's annotation must
  be cleaned explicitly. None of these are token-scope problems.

## Next evidence gates

1. **2026-11-01 03:00 UTC** — verify the weekly Velero run has no duplicate PVBs and
   the `VeleroScheduledBackupFailed` alert clears.
2. Repair/verify the runner-to-analyzer path and freeze independently verified
   declaration, timing, R1/R2 semantics, configuration and analysis identities.
3. Record owner and supervisor agreement on the one-stack comparator and R3
   exclusion separately; do not claim supervisor approval from owner direction.
4. Run a post-seed isolated recovery drill (database + seed file).
5. Obtain capacity/NTP/maintenance evidence from university IT; keep SMTP/CA/
   firewall and audit access as separately owned operational follow-ups.
6. Retain the deferred independent-copy limitation; implementation requires a
   separate storage/account decision and approval.
7. Revoke the drill MinIO key and decide on the retained drill PV/NFS directory.

## Recheck commands

```bash
kubectl get nodes -o wide
kubectl -n argocd get applications
kubectl -n invenio get hpa,pods -o wide
kubectl -n invenio get cronjob invenio-orphan-cleanup
kubectl -n invenio get configmap | grep invenio-app-config
kubectl -n monitoring get servicemonitor argocd-metrics
kubectl -n monitoring get prometheusrule | grep cluster-health
kubectl -n database get clusters.postgresql.cnpg.io,scheduledbackups.postgresql.cnpg.io
kubectl -n database get backups.postgresql.cnpg.io --sort-by=.metadata.creationTimestamp
kubectl -n velero get schedules.velero.io,backups.velero.io
kubectl -n invenio exec deploy/invenio-web -c web -- invenio files location list
```
