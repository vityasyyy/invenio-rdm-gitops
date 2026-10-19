# Velero backup failure diagnosis and plugin compatibility repair (#139)

**Status:** Deployed via PR #147 (merge `3f16f770`); post-merge live checks are
green. Recovery remains unproven, and the deployment exposed an unexpected
retention interaction: three expired Backup CRs disappeared, with storage deletion
explicitly logged for the August 14 backup. See the evidence below.
**Scope:** Velero values and data-mover config, backup schedule, pod-template
opt-in annotations, the Velero CI validator and its tests, this plan.
Restore procedure is owned separately.

## Confirmed live evidence (2026-10-19, context `btd`)

Latest backup `weekly-infra-backup-20261018030030` — `PartiallyFailed`,
started 2026-10-18T03:00:35Z, completed 03:15:41Z, **4 errors / 58 warnings**,
3458 items. Use the fully qualified `backups.velero.io`; unqualified `backup`
resolves to the CloudNativePG CRD.

All four errors are `PodVolumeBackup` failures, and they split into two causes.

| PVB | Volume | Pod | Cause |
|---|---|---|---|
| `...-jwpvw` | `export` | `minio-5797665d79-dpc6d` | **OOMKilled** after 771,707,282 bytes |
| `...-tdsz6` | `prometheus-...-db` | `prometheus-...-0` | **OOMKilled** after 106,157,967 bytes |
| `...-rbtz2` | `tmp` | `invenio-worker-55bbfbcc44-lh8w8` | `velero-quota` limits.cpu exhausted |
| `...-s7w78` | `tmp` | `invenio-worker-55bbfbcc44-v597l` | `velero-quota` limits.cpu exhausted |

### OOM evidence (durable, since the data-mover pods are gone)

The pods no longer exist, so `kubectl get pods` cannot show them. The termination
reason survives in kube-state-metrics and in the node-agent log:

- `kube_pod_container_status_terminated_reason{namespace="velero",pod=~"weekly-infra-backup-20261018030030-(jwpvw|tdsz6)"}`
  returns `reason="OOMKilled"` with value `1` at 2026-10-18T03:09:30Z/03:10:00Z
  (`jwpvw`) and 03:12:00Z/03:12:30Z (`tdsz6`). This per-container termination
  reason is the sufficient proof; it does not establish which OOM killer acted.
- `container_memory_working_set_bytes` peaks: `jwpvw` **241.2 MiB**,
  `tdsz6` **253.2 MiB**.
- `kube_pod_container_resource_limits{resource="memory"}` for both = **256 MiB**,
  inherited from the namespace `LimitRange` default, not set by Velero.
- Node-agent log: `Finish waiting data path pod, phase Failed, message ` followed
  by `VGDP started but termination event is not received` at 03:10:06Z
  (`jwpvw`) and 03:12:34Z (`tdsz6`). `Async fs backup data path failed` records an
  empty `error=` because the process was killed, not because kopia returned an
  error. The MinIO data-mover log block is empty, consistent with a kill during
  the first write.

Both failed volumes are the two largest live datastores in the backup. Worker-01
is not the cause: the same node completed a 19.6MB `tmp-dir` volume and others.

### Resource accounting

`velero-quota`: hard `limits.cpu=2`, `limits.memory=4Gi`, `pods=10`,
`requests.cpu=1`, `requests.memory=2Gi`. Used at inspection: `limits.cpu=750m`,
`limits.memory=768Mi`, `pods=3`.

The admission message for both Invenio worker `tmp` failures is exact:
`exceeded quota: velero-quota, requested: limits.cpu=250m, used: limits.cpu=2,
limited: limits.cpu=2`. At admission the quota already reported `limits.cpu` fully
consumed, so a 250m data mover could not be admitted.

The current 750m figure is idle-state usage, not the usage at 03:00–03:15Z on
2026-10-18. It does not establish the historical budget or how many data movers
ran concurrently; the concurrent count is unknown. 750m plus two 250m data movers
equals 1250m, which is still under the 2 CPU hard limit, so the observed idle
figure alone does not explain the rejection. The rejection is established by the
admission message, not by reconstructing the count.

Raising memory alone does not fix the quota rejections; raising CPU quota alone
does not fix the OOM kills.

## Deployment verification (merge `3f16f770`)

- 17/17 Argo CD Apps Synced/Healthy; live `velero-quota` matches the merged
  values (limits 6 CPU/9Gi, requests 1.5 CPU/8Gi, pods 20).
- Plugin `v1.13.2` init container exited 0 (digest `sha256:25c3a0a0…`); both
  node-agents carry uniform resources; maintenance `global` JSON and mover
  `podResources`/`loadConcurrency` are correct; web/Redis/Grafana opt-in
  annotations are present; BSL `Available`; public ping 200; database healthy.
- Node actual memory: worker-01 56%, worker-02 65%. No recovery jobs were
  created.

### Unexpected retention interaction — preserve-all expectation NOT met

Three expired Backup CRs disappeared during the deployment window; this was not
Argo prune or manual deletion:

- Server log 09:22:54 UTC: GC expiry `Aug14→Sep11`, `Aug30→Sep27`, `Sep20→Oct18`,
  with deletion requests at 09:22:54.
- 09:23:05: the Aug14 backup removed PodVolumeBackup snapshots and the backup
  data in object storage.
- Only `weekly-infra-backup-20261018030030` (PartiallyFailed, expires Nov15)
  remains.

The previous preserve-all expectation is not satisfied: existing TTL
garbage-collection resumed. Retention behavior must be reviewed before any
future Velero restart or plugin fix. Do not modify retention or run jobs as
part of this evidence work.

CI Deploy Verify passed about 6 s before the rollout began, so it did not cover
the deployed revision; later batches must gate on revision-specific live
verification.

## Supported plugin pair

- Chart `11.4.0` `Chart.yaml` declares `appVersion: 1.17.1`.
- Official matrix pairs AWS plugin `v1.13.x` with Velero `1.17.x`; the previously deployed
  `v1.11.1` is listed only for Velero `1.15.x`.
- `velero/velero-plugin-for-aws:v1.13.2` was locally downloaded for the Mac
  and deployed in `3f16f770`: the plugin init container exited 0 with digest
  `sha256:25c3a0a0…`. The registry path from worker-01 is reachable (`/v2`
  returns HTTP 401 as expected unauthenticated).

This plugin mismatch is a real risk repair but **not the root cause** of the four
errors; the OOM kills and the quota admission rejections are.

## Approved opt-in coverage policy (deployed in `3f16f770`)

Scope removal was replaced by an explicit allowlist. The schedule now runs with
`defaultVolumesToFsBackup: false`, so only pods carrying
`backup.velero.io/backup-volumes` get file-system backup. Namespaces and
manifests stay in scope; nothing is excluded wholesale.

| Workload | Selected volume | Why |
|---|---|---|
| `invenio-web` | `data`, `archive` | Durable application files |
| `redis-master` | `data` | Durable AOF/RDB; broker/results/sessions are not disposable |
| `monitoring-grafana` | `storage` | Dashboards, Grafana DB, plugins |

Deliberately **not** selected:

- Invenio and worker `tmp` `emptyDir` volumes — scratch, and the source of the
  two quota rejections.
- MinIO `export` — it holds the Velero backup objects themselves; backing the
  backup store into itself is not recovery.
- Prometheus TSDB — a live self-mutating store with a 10Gi NFS volume.
- PostgreSQL `pgdata` — recovered natively through CloudNativePG, not by
  file-system copy of a live database.
- OpenSearch — rebuildable from Postgres; rebuild policy instead of a data copy.

`invenio-worker` mounts the shared `invenio-data` RWX claim without an
annotation; only the web pods that declare both durable volumes opt in, so the
shared claim is copied once per backup rather than per replica.

### Resource budget — bounded

`node-agent-config.yaml` sets mover limits 250m/1Gi with memory request 1Gi,
`loadConcurrency.globalConfig: 1` (per node) and nested `prepareQueueLength: 1`
(conservatively counted per node too). `values.yaml` pins node-agent pods at
250m/256Mi and repository maintenance jobs at 250m/256Mi with memory request
equal to the limit, so each mover/job reserves its full possible footprint
before scheduling. The live `--repo-maintenance-job-configmap=velero-repo-maintenance`
is now chart-rendered from Git instead of surviving only as cluster drift.

Worst-case simultaneous footprint (server rollout surge = 1 extra server pod,
since Deployment maxSurge rounds up; node-agent DaemonSet has maxSurge 0):

| Component | Count | CPU limit | Memory limit | CPU request | Memory request |
|---|---|---|---|---|---|
| Velero server (+surge) | 2 | 1000m | 1Gi | 100m | 256Mi |
| node-agent | 2 | 500m | 512Mi | 100m | 128Mi |
| data movers (active+prepared) | 4 | 1000m | 4Gi | 400m | 4Gi |
| maintenance jobs | 10 | 2500m | 2.5Gi | 500m | 2.5Gi |
| **Total** | **18 pods** | **5000m** | **8192Mi** | **1100m** | **7040Mi** |
| `velero-quota` hard | | 6 | 9Gi | 1500m | 8Gi |

Headroom: 1000m/1Gi limits, 400m/1152Mi requests, 2 pods. Init-container
effective requests: the plugin init container inherits LimitRange defaultRequest
(50m/64Mi), below the server container sums, so the effective pod sizes above
are unchanged; the LimitRange max (1 CPU/1Gi) still caps unsized containers.

Assumptions and limits:

- Two schedulable nodes (control-plane tainted). The mover bound is 2 per node
  because `globalConfig` is per node; prepared movers are counted per node, so
  if the controller applies `prepareQueueLength` once cluster-wide this budget
  is conservative.
- Maintenance streams ≤ `includedNamespaces` (10; 9 repositories live). The
  validator derives this bound from the schedule, so adding a namespace raises
  the computed budget and fails CI until the quota is reviewed — the
  future-growth guard.
- `velero-quota` is an admission ceiling across both nodes, not reserved node
  memory: worker request floors at review were 5302Mi/7918Mi and 4882Mi/7918Mi,
  and existing limits are already overcommitted. Requests make new movers/jobs
  reserve their full memory footprint; under peak they can still remain
  Pending, and node memory pressure stays monitored. Re-measure live headroom
  at the deployment gate.
- No absolute node-OOM guarantee: existing workloads overcommit and actual
  usage can exceed requests. This budget bounds the namespace's worst-case
  admission; it does not reserve cluster memory.

**Halt gate:** if live request headroom on either worker at deployment is below
one mover request (1Gi memory) plus agent request (64Mi), or the repository
count exceeds the scheduled namespace count, stop and escalate rather than
raising the quota further.

### Compatibility and wiring

- The node-agent receives the ConfigMap through
  `nodeAgent.extraArgs: --node-agent-configmap=node-agent-config`. The actual
  v1.17.1 binary's `node-agent server --help` lists
  `--node-agent-configmap string`; the v1.17 docs show `--node-agent-config`,
  which is stale for this build.
- Grafana's `podAnnotations` key is a chart passthrough into the pod template;
  verified against the cached `kube-prometheus-stack` 69.6.0 artifact
  (`grafana` subchart 8.10.1) and by rendering the deployment.
- The plugin pin (`v1.13.2`) remains a separate supported-pair repair; it is
  deployed and its init container exits 0.

### Deployment record and future gates

1. **Pod-template rollout.** Adding annotations to `invenio-web` and
   `redis-master` changes their pod templates, so both Deployments roll. Redis
   has `maxSurge: 0` and `maxUnavailable: 1`, so its single replica is briefly
   unavailable. This must be scheduled, not treated as a no-op sync.
2. **Velero restart.** The node-agent reads the ConfigMap at startup; the
   daemonset rolls and Velero restarts.
3. **Image availability.** Executed: plugin init container exited 0 on the
   pulled digest `sha256:25c3a0a0…`.
4. **Live headroom re-measure.** Node resource figures must be current at the
   deployment gate; a repository count above the scheduled namespace count also
   trips the halt gate above.
5. **Quota is computed, not inflated.** `velero-quota` is sized from the frozen
   worst case; any further raise requires re-running the budget, not extra
   headroom on its own.
6. **Retention review.** Confirm TTL/GC behavior before any future Velero
   restart or plugin fix; do not modify retention or run jobs as part of the
   evidence work.

## Rejected alternatives

- **Blindly enlarging `velero-quota`.** The quota is now derived from the
  frozen worst case; extra ceiling without a budget change adds node pressure
  and does not fix OOM kills.
- **Excluding the MinIO and Prometheus namespaces or manifests.** Removes
  unrelated objects from scope and hides the coverage gap instead of stating it.
- **Backing up the MinIO volume.** Self-referential; the backup store cannot
  contain a consistent copy of itself.

Any future change to which volumes are backed up must extend the ownership
documented in `2026-10-15-velero-ownership.md` for the affected namespaces.

## Tests and verification

- `python3 scripts/tests/test_ci_validate_velero.py` — 21 tests passed.
- `python3 scripts/ci-validate-velero.py` — passed.
- `python3 -m unittest discover -s scripts/tests` — 26 tests passed.
- Budget tests freeze the worst case (5000m/8192Mi limits, 1100m/7040Mi
  requests, 18 pods), fail when the quota is lowered or a mover memory request
  drops below its limit, and fail when schedule growth outruns the quota.
- `python3 -m yamllint k8s/infra/velero/values.yaml` and
  `kustomize build k8s/infra/velero` — passed.
- Regression evidence: the plugin-series test failed before the pin change with
  `Velero 1.17.x requires AWS plugin 1.13.x, found v1.11.1`.

No backup or restore job was launched. No object-store object was deleted.

## Rollback

Revert the plugin tag only if the supported pair fails live; CI will then fail the
compatibility gate by design, so record the exception here rather than bypassing
it. Data-mover sizing, if later approved, is reverted by removing the ConfigMap
reference; the namespace LimitRange defaults then apply again.
