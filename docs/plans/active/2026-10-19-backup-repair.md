# Velero backup failure diagnosis and plugin compatibility repair (#139)

**Status:** DRAFT — not deployable. Root cause confirmed live; opt-in coverage and
data-mover configuration are prepared and tested. The worst-case quota budget is
unresolved and requires an explicit owner decision.
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

## Supported plugin pair

- Chart `11.4.0` `Chart.yaml` declares `appVersion: 1.17.1`.
- Official matrix pairs AWS plugin `v1.13.x` with Velero `1.17.x`; the deployed
  `v1.11.1` is listed only for Velero `1.15.x`.
- `velero/velero-plugin-for-aws:v1.13.2` exists in the registry (multi-arch
  manifest resolves), **not** in the local image cache. Deployment needs a node
  pull, which is gated by the standing download rule.

This plugin mismatch is a real risk repair but **not the root cause** of the four
errors; the OOM kills and the quota admission rejections are.

## Approved opt-in coverage policy (prepared, not deployed)

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

### Data-mover budget — unresolved

`node-agent-config.yaml` sets `memoryLimit: 1Gi` (above the 253MiB observed peak
and the 256Mi LimitRange default) and `cpuLimit: 250m`, with
`loadConcurrency.globalConfig: 1` and `prepareQueueLength: 1`.

**`globalConfig` is per node, not a cluster-wide cap.** The live daemonset has
**2** node-agents (the control-plane node is tainted `CriticalAddonsOnly`), so up
to two active data movers can run. `prepareQueueLength: 1` is a global queue
limit for CRs waiting in Accepted/Prepared, and each of those holds a hosting pod
sized with the same `podResources`.

Worst case: 2 nodes × (1 active + 1 prepared) = 4 data-mover pods.

| Component | CPU | Memory |
|---|---|---|
| Velero server | 500m | 512Mi |
| node-agent ×2 | 500m | 512Mi |
| 4 data-mover pods | 1000m | 4Gi |
| **Total** | **2000m** | **≈ 5Gi** |
| Quota hard limit | 2000m | 4Gi |
| **Remaining** | **0m** | **−1Gi** |

The worst case consumes the entire CPU hard limit and **exceeds the memory hard
limit**. It also leaves nothing for the recurring kopia repository maintenance
jobs (250m/256Mi each, currently succeeding). A single active mover per node with
no prepared pod fits (1500m / 2.5Gi), but that state is not guaranteed by the
configuration.

**This budget is not proven to fit `velero-quota`.** Deployment is therefore
gated on an explicit decision; no quota change was made. Options: accept a
single-node backup window with the other node-agent excluded, lower the
data-mover `memoryLimit`, raise `velero-quota` deliberately, or accept that
admission failures can still occur under worst-case concurrency.

### Compatibility and wiring

- The node-agent receives the ConfigMap through
  `nodeAgent.extraArgs: --node-agent-configmap=node-agent-config`. The actual
  v1.17.1 binary's `node-agent server --help` lists
  `--node-agent-configmap string`; the v1.17 docs show `--node-agent-config`,
  which is stale for this build.
- Grafana's `podAnnotations` key is a chart passthrough into the pod template;
  verified against the cached `kube-prometheus-stack` 69.6.0 artifact
  (`grafana` subchart 8.10.1) and by rendering the deployment.
- The plugin pin (`v1.13.2`) remains a separate supported-pair repair; its image
  pull is still gated by the standing download rule.

### Deployment gates (explicit)

1. **Pod-template rollout.** Adding annotations to `invenio-web` and
   `redis-master` changes their pod templates, so both Deployments roll. Redis
   has `maxSurge: 0` and `maxUnavailable: 1`, so its single replica is briefly
   unavailable. This must be scheduled, not treated as a no-op sync.
2. **Velero restart.** The node-agent reads the ConfigMap at startup; the
   daemonset rolls and Velero restarts.
3. **Image pull.** `velero-plugin-for-aws:v1.13.2` still needs a node pull.
4. **Worst-case quota budget is unresolved** (see above). Do not deploy on the
   assumption that one data mover runs cluster-wide.
5. **No quota change.** `velero-quota` is untouched; if a future volume needs
   more, escalate rather than inflate.

## Rejected alternatives

- **Enlarging `velero-quota`.** Raises concurrent data-mover pressure on 8 CPU
  nodes and does not fix OOM kills.
- **Excluding the MinIO and Prometheus namespaces or manifests.** Removes
  unrelated objects from scope and hides the coverage gap instead of stating it.
- **Backing up the MinIO volume.** Self-referential; the backup store cannot
  contain a consistent copy of itself.

Any future change to which volumes are backed up must extend the ownership
documented in `2026-10-15-velero-ownership.md` for the affected namespaces.

## Tests and verification

- `python3 scripts/tests/test_ci_validate_velero.py` — 9 tests passed.
- `python3 scripts/ci-validate-velero.py` — passed.
- `python3 -m unittest discover -s scripts/tests` — 14 tests passed.
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
