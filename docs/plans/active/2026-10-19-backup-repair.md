# Velero backup failure diagnosis and plugin compatibility repair (#139)

**Status:** Root cause confirmed live. Prepared repair is the supported plugin pair;
data-mover sizing and coverage are policy decisions, not implemented.
**Scope:** Velero values, Velero CI validator and its tests, this plan.
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
  (`jwpvw`) and 03:12:00Z/03:12:30Z (`tdsz6`).
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
- No node-level OOM (`node_vmstat_oom_kill` empty); this was a container memory
  limit kill on worker-01.

Both failed volumes are the two largest live datastores in the backup. Worker-01
is not the cause: the same node completed a 19.6MB `tmp-dir` volume and others.

### Resource accounting

`velero-quota`: hard `limits.cpu=2`, `limits.memory=4Gi`, `pods=10`,
`requests.cpu=1`, `requests.memory=2Gi`. Used at inspection: `limits.cpu=750m`,
`limits.memory=768Mi`, `pods=3`. Two data movers at the 250m LimitRange default
consume 500m of the remaining 1.25 CPU; a third is rejected, which is exactly the
two Invenio worker `tmp` failures. Raising memory alone does not fix this; raising
CPU quota alone does not fix the OOM kills.

## Supported plugin pair

- Chart `11.4.0` `Chart.yaml` declares `appVersion: 1.17.1`.
- Official matrix pairs AWS plugin `v1.13.x` with Velero `1.17.x`; the deployed
  `v1.11.1` is listed only for Velero `1.15.x`.
- `velero/velero-plugin-for-aws:v1.13.2` exists in the registry (multi-arch
  manifest resolves), **not** in the local image cache. Deployment needs a node
  pull, which is gated by the standing download rule.

This plugin mismatch is a real risk repair but **not the root cause** of the four
errors; the OOM kills and the quota accounting are.

## Proposed options (policy decision required — not implemented)

No scope removal, no quota inflation, and no silent coverage reduction was made.
All backup objects are preserved.

1. **Data-mover memory sizing (recommended first).** Velero 1.17 supports
   `podResources` in a node-agent ConfigMap referenced by `--node-agent-configmap`.
   Raise `memoryLimit` above the observed 253MiB peak with headroom; keep CPU at
   the current default. This directly addresses the two OOM kills without
   changing what is backed up.
2. **Concurrency bound.** Bound concurrent data movers so the CPU quota is not
   exhausted by parallelism, instead of enlarging `velero-quota`. This addresses
   the two Invenio `tmp` failures.
3. **Coverage decision (owner).** MinIO `export` holds all Velero backup objects
   and the Prometheus TSDB is a live self-mutating store. Options are: keep both
   with sufficient memory, exclude them with an explicit documented gap, or move
   them to a native/opt-in path. Each changes data coverage and needs an explicit
   owner choice. MinIO in particular is the backup-of-backups case and cannot be
   silently dropped.
4. **Quota increase (not recommended yet).** Enlarging `velero-quota` raises
   concurrent data-mover pressure on 8 CPU nodes and does not fix OOM kills.

Any change to which volumes are backed up must extend the ownership documented in
`2026-10-15-velero-ownership.md` for the affected namespaces.

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
