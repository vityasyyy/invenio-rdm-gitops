# Recovery closeout and backup truthfulness (#139)

Status: two-worker Wave1 approved2026-10-20. Production-reliability principles and
repository conventions apply; unavailable workflow skills are not invoked.

## Scope and owners

| Owner | Scope |
|---|---|
| recovery-closeout | Exact drill inventory/procedure, remove two temporary security rules, dedicated regression and closeout plan |
| backup-freshness | Required-backup metric source, alert/dashboard wiring, monitoring tests and plan |
| Lead | Preserve evidence, independently verify ownership, execute approved cleanup, review/integrate, index and this plan |

Workers use separate worktrees/workspaces from`fb21f83`. No worker cluster writes,
secret access, installs/downloads, host changes, push or merge. Production manifests,
backup objects and unrelated Released PVs remain untouched by cleanup.

## Sequence and verification

1. Inventory exact canary source/destination and native recovery namespace resources;
   capture three known drill PVC/PV identities, owners and backing paths before deletion.
2. Stop/delete approved drill resources without force/finalizer bypass; preserve
   Backup/Restore records and reports. Retain storage metadata/backing data unless its
   exact removal scope is independently verified and approved. MinIO backend key
   revocation requires the owner, not reading production credentials.
3. Remove the two temporary rules through reviewed PR/CI after dependent workloads
   stop; verify absence live and verify production remains healthy.
4. Replace global-gauge freshness with a source that distinguishes required production
   coverage from canary/manual success, and failures from success. Inspect deployed
   metric labels and version-specific collector behavior before choosing the design.
5. Test stale-production/fresh-canary, missing objects/metrics, PartiallyFailed,
   valid required success and restarts. Independently render/test/lint and review.
6. Deployment remains gated on explicit approval of the concrete monitoring change;
   after approval verify post-merge pipeline, exact live config, evaluated metrics/rules
   and service health. Green early DeployVerify alone is insufficient.

## Acceptance boundaries

- Quota warnings disappear by retiring completed drills, not alert suppression or
  arbitrary quota inflation. RetainPV metadata/folders and backend credentials are
  separate, explicit residuals until removal/revocation is evidenced.
- Canary/manual success cannot certify required scheduled coverage. Production manual
  backup and schedule execution may need separate signals; do not forge schedule labels.
- Cleanup does not erase the proof limitations: seeded Invenio record/file consistency,
  broker/Grafana recovery, independent NFS/MinIO DR and workload-scaleRPO/RTO remain open.
- Later waves: scheduler and Loki first, then CI/build/migration safeguards, then an
  explicit experiment baseline. New evidence can reorder them; no automatic next-wave
  spawning or deployment is authorized by this plan.

## Executed cleanup evidence

Lead verified the three expected PV UIDs, unique CSI handles, Retain policies and
absence of ArgoCD Application destinations before deletion; inventory saved in the
approved session artifact directory. Native Cluster foreground deletion completed,
then the three explicit namespaces were deleted without force. All three PVs are
Released with original UIDs; backing directories remain. All three Velero Backup
UIDs remain unchanged. Seventeen Applications Synced/Healthy, production database
healthy and public ping200 after cleanup. Backend key revocation is not implied by
namespace Secret removal. Prometheus alert-resolution verification remains pending
because the lead's two read-only query transports failed; no alert was silenced.

Subsequent worker query returned no drill quota alerts. Cleanup PR #153 merged at
`9914811`; Deploy Verify run `39364095716` passed, security Application reached that
revision and both temporary network policies were independently confirmed absent.
This repository exposes no `Deploy Production` or `Infra Apply (auto)` workflows;
the available post-merge workflow is `Deploy Verify`, supplemented by live checks.

The monitoring worker delivered production completion and independent weekly
execution signals with regression coverage. Lead review required completion rather
than CR creation time, explicit absent-coverage handling, and the configured 4h
operation grace. See [monitoring plan](2026-10-20-required-backup-monitoring.md).
The monitoring deployment remains gated; later worker waves have not been launched.
