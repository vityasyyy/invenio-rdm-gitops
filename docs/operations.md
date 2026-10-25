# Operations

## Validate without deployment

```bash
python3 -B -m unittest discover -s scripts/tests
python3 scripts/manifest_sources.py --list
yamllint argocd/ k8s/
bash scripts/ci-validate-monitoring.sh
python3 scripts/ci-validate-velero.py
```

`scripts/ci-render-manifests.sh --output <empty-directory>` renders the actual
Application sources and fails on missing values or render errors. It requires
Python 3/PyYAML, Helm and Kustomize already installed; it never installs tools.
Normal rendering fetches pinned charts and upstream raw manifests. In a
no-download session, use already-cached charts (`--charts <directory>` containing
`chart-version.tgz`) and render only local Kustomize inputs; report unfetched
upstream sources explicitly. Do not run remote render commands as an offline test.
Existing output must be empty to avoid validating stale objects as fresh output.

## Day-to-day checks

```bash
kubectl -n argocd get applications
kubectl -n invenio get deployments,pods,hpa
kubectl -n monitoring get pods
kubectl -n velero get schedules,backups,backupstoragelocations
kubectl -n database get clusters.postgresql.cnpg.io,backups.postgresql.cnpg.io
```

Use a request timeout for remote diagnostics. Synced/Healthy, completed Jobs and
green CI are different signals; none alone proves public-path health or recovery.
Bootstrap is not a repair operation and must not be run on the existing cluster.
Do not rerun setup, migrations, fixture imports, reindexing or queue operations
while diagnosing an ordinary reconciliation failure.

## Metrics and logs

Existing Grafana views retain their UIDs: see [incident triage](../README.md#incident-triage).
Loki/Alloy and Prometheus have different loss/retention semantics. Quiet application
traffic is not proof of a broken log pipeline; absent metrics are unknown, not zero.
Inspect backend reachability, scrape targets and metric labels before changing an
alert. Historical counters are not current failure rates.

## Backups and recovery boundaries

See the preserved [backup and restore runbook](../README.md#backup--restore).
Velero weekly backups retain672h (28d); Prometheus and Loki retain7d. CNPG remains
at30d until a usable base-backup/WAL chain and compatible application-file recovery
are established. A reported recoverability point is not a tested restore.

- Inspect each scheduled run's actual start/completion, phase, errors, scope labels
  and PodVolumeBackups. A manual/canary completion cannot satisfy the weekly run.
- Match alert labels and firing timestamps to the run before interpreting Discord
  messages; no active alert now does not explain an earlier notification.
- Canceled volume backups and an unfinished Backup are not successful coverage,
  even when a different Pod's backup of a shared PVC completed.
- Bound manual backup TTLs explicitly. Inspect Velero deletion failures and Kopia
  repository maintenance outcomes; completed maintenance alone does not prove bytes
  were reclaimed. Measure bucket bytes/object counts separately.
- Database cleanup must use Barman's dependency-aware retention so retained base
  backups keep required WAL. Never apply blanket bucket expiry or manual purges.
- Keep file backups, database recovery time and external stores consistent; do not
  infer application-level consistency from Kubernetes object backup completion.

No cancellation/retry, cleanup, restore or retention reduction is an authorized
diagnostic action. Preserve evidence and seek deployment/data-operation approval.

## Source-path adoption and rollback

The [repository map](repository-map.md) is authoritative. Structural refactors
must preserve complete rendered objects and all tracking/storage identities.
Review the Argo diff before approved adoption; stop on unexpected deletion,
recreation, permissions or ownership changes. Revert the path/value-reference
commit as a unit to restore selectors and source paths, not individual Secrets.
Do not reset installations, remove finalizers or reseal secrets for a path move.
