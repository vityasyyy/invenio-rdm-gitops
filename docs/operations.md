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

## Initialization versus routine sync

The PostSync `invenio-setup` hook is read-only. It checks that every current
Alembic revision is an image head, the default file location, search index aliases,
declared queues, and the admin superuser permission; any missing postcondition fails
the hook with an actionable message. It never migrates, imports fixtures, declares
queues or rebuilds indexes. A red hook is an inspection signal, not a trigger to run
the full initialization Job; use targeted approved remediation for the specific
missing state.

Initialization is a separate manual artifact:
`k8s/apps/invenio/initialization/`. It is deliberately not referenced by
`k8s/apps/invenio/kustomization.yaml`, so ArgoCD cannot run it as part of an ordinary
sync or measured repair. The directory's Kustomization applies the same shared
image-digest component as the application, so do not apply the raw Job directly:

```bash
# Approval required; do not use as a routine repair operation.
kustomize build k8s/apps/invenio/initialization | kubectl apply -f -
kubectl -n invenio wait --for=condition=Complete job/invenio-initialize --timeout=2h
kubectl -n invenio logs job/invenio-initialize
```

Do not rerun it merely because the verification hook is red; first inspect which
postcondition is missing and use the smallest targeted, approved action. The full
initialization performs migrations, fixtures and a reindex, so it is only for a
verified incomplete initial setup. A routine `OutOfSync` repair must not become an
implicit fixture import, reindex or queue declaration. The old hook's broad `|| echo`
and `|| true` handling hid those distinctions and is intentionally removed.

Observed live drift: the default file location is currently `s3://invenio-rdm/files/`,
while the old setup command expected a local `$INVENIO_INSTANCE_PATH/data` path.
Verification therefore checks that a default location exists rather than assuming
which backend is correct; changing the backend is a separate data migration.

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

### Retention change gate

Current retention is Velero `672h` (28d) for the weekly schedule, Prometheus and
Loki `7d`, and CNPG `30d`. Do not reduce CNPG to 7d until all of the following are
demonstrated, not inferred:

1. Barman can list a contiguous base-backup/WAL chain for the proposed 7d window.
2. A restore drill from that chain reaches a verified application/database state.
3. The matching Velero file backup is complete for the same window, with no canceled
   volume backups; database and file timestamps are compatible for the chosen RPO.
4. Barman dependency-aware cleanup removes expired bases only with their unneeded
   WAL, and the object store usage actually decreases.
5. The cleanup result is visible (deletion attempts/failures), and no manual or
   blanket bucket expiry is used.

Observed 2026-10-25: MinIO reported about 36.5GB used and 10,904 objects; Velero
deletion metrics showed four attempts and four successes with zero failures. CNPG
had 30d of hourly completed backups, oldest recoverability point 2026-09-25, and no
recent failed backup; 1,871 older failed Backup CRs from August remain as historical
API clutter with node-proxy 502 errors. These are status observations, not proof of
WAL integrity, restore success, or compatible file recovery. Retention therefore
stays unchanged.

A scheduled Backup can remain `InProgress` until its 4h item-operation timeout after
a canceled volume backup, then become `PartiallyFailed`. A canceled PVB is not
coverage even when another pod's backup of the same shared PVC completed. Do not
cancel, retry or purge to shorten that window without approval.

No cancellation/retry, cleanup, restore or retention reduction is an authorized
diagnostic action. Preserve evidence and seek deployment/data-operation approval.

## Source-path adoption and rollback

The [repository map](repository-map.md) is authoritative. Structural refactors
must preserve complete rendered objects and all tracking/storage identities.
Review the Argo diff before approved adoption; stop on unexpected deletion,
recreation, permissions or ownership changes. Revert the path/value-reference
commit as a unit to restore selectors and source paths, not individual Secrets.
Do not reset installations, remove finalizers or reseal secrets for a path move.

## Image update precedence

`k8s/apps/invenio/image-pin/kustomization.yaml` is the single Git digest source.
The application Kustomization includes it as a component; the manual initialization
Kustomization includes the same component. The ImageUpdater CR targets
`invenio-bootstrap`, tracks `ghcr.io/vityasyyy/invenio-ugm` by digest and uses
`writeBackConfig.method: argocd`.

ArgoCD's Application-level `spec.source.kustomize.images` override is applied after
the component, so the updater's API-written digest wins while it is present. The
Application manifest intentionally has no static `kustomize.images`; adding one
would create a second Application-level source. The live Application had no
override at the 2026-10-25 inspection (`imagesManaged: 0`), so the component digest
was effective. The manual initialization Job is outside ImageUpdater's scope.

To change the Git fallback, edit only the component. To freeze an image for an
experiment, pin the component and freeze/suspend the updater; do not add another
editable digest. The precedence and updater contract are covered by
`scripts/tests/test_image_update_contracts.py`; the application override is a
runtime write-back, not durable Git desired state.
