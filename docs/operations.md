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

## Worker health checks

Worker startup/liveness/readiness probes use the image-packaged
`worker_healthcheck.py`, not a full Invenio import. Success requires the exact
own-node broker pong; MessagePack/JSON replies are accepted and broker values
are never printed. The reply wait is not a total connection deadline: kubelet
`timeoutSeconds` remains the outer execution limit.

For future changes, deliver and verify a helper-bearing image **before** a
probe command references it. Roll back probe commands independently if needed;
do not add a silent missing-helper fallback. See
[worker probe findings](worker-probe-findings.md). Do not infer sustained memory
savings or experiment readiness from a successful ping alone.

The six-hour background acceptance window is recorded in the findings. It does
not verify loaded task progress: Redis `vocabularies` still held 264,140 messages
on 29 October. Do not purge, unboundedly drain or submit synthetic tasks as a
routine health check. Missing task/queue metrics are an evidence gap, not zero.

For backup status, use fully qualified `backups.velero.io` and
`backups.postgresql.cnpg.io`; unqualified `backups` can select the wrong CRD.
The 29 October daily PostgreSQL run completed. Actual Sunday Velero success is
still pending 1 November 03:00 UTC; manual recovery proof does not replace it.
The operational file backup remains suspended and has no successful point or
restore proof; see [its runbook](#operational-s3-file-backup-suspended).

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

Placement is chosen by the Kubernetes scheduler; node names in logs are current
bindings, not fixed assignments. See
[scheduling and placement](architecture-guide.md#scheduling-and-placement) and the
[current verified state](current-state.md) for the recorded placement, schedules and
experiment-readiness gates.

Schema verification compares database revisions with Alembic's dependency-resolved
heads through its API. Raw `heads` counts and arrow-formatted CLI output are not
evidence of pending migrations: dependency-only heads need not have separate rows.

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

The default file location is intentionally `s3://invenio-rdm/files/`. Verification
checks that a default location exists rather than assuming which backend is correct;
changing the backend is a separate data migration.

## Declarative changes and exceptions

Routine desired-state changes go through Git and ArgoCD. All 18 Applications use
automated sync and self-heal; `argocd-self` and `sealed-secrets` intentionally have
`prune: false`. Do not use `kubectl rollout restart`, `kubectl apply`, or manual
Argo sync for a normal change.

Deliberate imperative exceptions are bounded and documented:

- first cluster/ArgoCD bootstrap (`scripts/bootstrap-infra.sh`);
- one-off full initialization/migrations
  (`k8s/apps/invenio/initialization/`);
- secret creation, rotation and revocation, which must never be in Git;
- data operations such as seed/reset and restore;
- break-glass recovery of a stuck Argo operation or hook.

An exception is not a routine repair. Record the reason and evidence.

## Configuration changes

The non-secret application config lives in
`k8s/apps/invenio/config/app-config.env`. The shared `config` Component runs it
through `configMapGenerator`; a content change produces a new
`invenio-app-config-<hash>` name and updates every `envFrom` reference, so ArgoCD
rolls web, worker and scheduler. Never force a rollout manually; if Argo is
OutOfSync, inspect the Application and the rendered diff.

## Orphan file cleanup

`invenio-orphan-cleanup` runs daily at 04:15. It derives live S3 URIs from
`files_object` references, removes unreferenced objects under `invenio-rdm/files`
with single-object `rm_file()` calls, then clears orphan `FileInstance` rows with
`clear_orphaned_files(force_delete_check=True)`.

Do not use `s3fs.rm()` for this bucket: MinIO rejects its bulk delete with
`MissingContentMD5`. The packaged CronJob was verified with a one-off Job after a
draft delete; it removed one orphan and preserved the seed. Check its Job logs and
the next scheduled run after changes.

## GitOps monitoring

Prometheus scrapes `argocd-metrics` from `argocd-application-controller:8082` through
a ServiceMonitor and a least-privilege network policy. The `argocd` rule group
alerts on non-Synced, unhealthy, and operation-running-for-20m Applications.
Dashboard `05 GitOps & Reconciliation` shows the same state plus HPA and config
evidence.

If Argo panels or alerts are empty:

1. Check `kubectl -n monitoring get servicemonitor argocd-metrics`.
2. Check the Prometheus target `up{job="argocd-metrics"}`.
3. Check the `allow-monitoring-to-argocd-metrics` NetworkPolicy and port 8082 in
   Prometheus egress.
4. Check the `argocd` rule group in Prometheus.
5. Do not interpret an absent series as healthy; it is unknown until scraped.

## Metrics and logs

Existing Grafana views retain their UIDs: see [incident triage](../README.md#incident-triage).
Loki/Alloy and Prometheus have different loss/retention semantics. Quiet application
traffic is not proof of a broken log pipeline; absent metrics are unknown, not zero.
Inspect backend reachability, scrape targets and metric labels before changing an
alert. Historical counters are not current failure rates.

## Backups and recovery boundaries

See the preserved [backup and restore runbook](../README.md#backup--restore).
Velero weekly backups retain672h (28d); Prometheus and Loki retain7d. CNPG runs
daily at02:00 UTC and retains30d. The25October drill verified database recovery, but
dependency-aware cleanup and file-store compatibility are still unproven, so retention
stays30d. Invenio file bytes are in MinIO, which is also the backup store and therefore
a single failure domain; no off-site copy exists. A reported recoverability point is
not a tested restore.

- MinIO is both the primary file store and the backup store on the same NFS. Do not
  claim independent disaster recovery until an off-site or otherwise independent
  copy exists and its recovery is verified.
- Inspect each scheduled run's actual start/completion, phase, errors, scope labels
  and PodVolumeBackups. A manual/canary completion cannot satisfy the weekly run.
- Match alert labels and firing timestamps to the run before interpreting Discord
  messages; no active alert now does not explain an earlier notification.
- Shared RWX volumes are not opted in once per replica; duplicated backups delayed a
  weekly run and made it partial. A canceled PVB is not coverage even when another
  pod's copy of the same PVC completed.
- Bound manual backup TTLs explicitly. Inspect Velero deletion failures and Kopia
  repository maintenance outcomes; completed maintenance alone does not prove bytes
  were reclaimed. Measure bucket bytes/object counts separately.
- Database cleanup must use Barman's dependency-aware retention so retained base
  backups keep required WAL. Never apply blanket bucket expiry or manual purges.
- Keep file backups, database recovery time and external stores consistent; do not
  infer application-level consistency from Kubernetes object backup completion.

### Operational S3 file backup (suspended)

The repository contains a **suspended** `invenio-rdm-file-backup` CronJob and one
command source, `k8s/infra/minio/resources/s3-file-backup.py`. The command and
manifests are offline-tested only. No destination bucket, dedicated Secret,
credential, activation, scheduled success, retained point or restore drill has been
performed. The existing private inventory key is not a backup-writer or restore
credential.

Scope and integrity:

- Sources are the entire `invenio-rdm` and `invenio-rdm-uploads` buckets. Completed
  temporary objects are included and an empty staging bucket is explicit in the
  manifest. Native unfinished multipart transfers are counted, never aborted or
  completed; users restart unfinished transfers after recovery.
- Destination is the private `invenio-rdm-file-backups` bucket, owned prefix
  `snapshots/`. It is not a deletion-following mirror. Source versioning, lifecycle
  and ACL settings are not changed. Source versioning must stay unconfigured for
  full-bucket coverage; a configured state fails the run rather than claiming version
  coverage.
- Each point has a unique UTC/random identity, paginated source-listing bounds,
  source settings and aggregate incomplete-upload counts. Objects are acquired with
  a conditional `If-Match` source identity where available.
- Bytes are streamed with explicit SDK multipart calls in filled 8 MiB parts, one
  transfer at a time, and hashed with SHA-256. The observed cached s3transfer
  0.10.3 runtime was not inspected under this offline scope; a local 0.14.0 source
  check showed its non-seekable read path does not fill short reads. The command
  therefore does not assume managed short-read or buffering behavior. One part is
  resident; the S3 10,000-part limit caps a supported object at 80,000 MiB
  (about 78 GiB) at this chunk size. The destination is read back and verified
  for every object. ETag is a source identity/precondition, not a universal
  content MD5. Standard content headers and user metadata are preserved; object
  tags are not copied.
- A second source inventory detects observed changes. A source change, missing
  object, permission error, partial read/write or verification mismatch fails the
  run without a completion marker. The partial prefix remains as failure evidence.
- The private manifest and `COMPLETE.json` marker are written only after all
  verification succeeds; the marker binds the manifest SHA-256. Acquisition is
  sequential and non-atomic. It is not a PostgreSQL/file snapshot and does not prove
  application-level consistency.
- The command has no application imports, database mount, PVC, Kubernetes API token
  or application/root credential. The job is pinned to the observed cached runtime
  digest
  `ghcr.io/cloudnative-pg/postgresql@sha256:99be063781d171d3971089b49c992706bdab9ccbd2b57cdf126c7542773aedfe`
  with the command overridden to Python. The CNPG Cluster image tag is separate and
  unchanged. Deployment must still verify the pullable reference, node cache and
  placement; the digest is not proof of a cross-registry pull.

The suspended job proposes daily 02:30 UTC, `Forbid`, starting grace 300s and active
deadline 900s, requests 100m/64Mi and limits 500m/256Mi. It does not change existing
HPA, resource budgets, capacity gates or the 70% research gate. Adding the recurring
workload still requires deployment approval and apparatus re-freeze.

Monitoring is narrow and suspended-aware: `S3FileBackupJobFailed`,
`S3FileBackupNeverSucceeded` and `S3FileBackupStale` are gated on
`kube_cronjob_spec_suspend == 0`; the proposed stale window is 36 hours. Terminal
failure uses the Job Failed condition (`kube_job_failed{condition="true"} == 1`)
with CronJob ownership from `kube_job_owner`; failed-pod counters and retry gaps
are not terminal. Actual kube-state-metrics availability, selectors and success
semantics must be verified after deployment. Job completion is an outcome proxy,
not proof that a retained point restores; inspect the private marker and run an
isolated restore.

After separate approval, a manual backup is a live destination write:

```bash
kubectl -n minio create job \
  --from=cronjob/invenio-rdm-file-backup \
  invenio-rdm-file-backup-manual-$(date -u +%Y%m%d-%H%M%S)
```

A restore must run in a separately provisioned drill scope and is not performed by
the CronJob:

```bash
SNAPSHOT_ID=20261030T023000Z-0123456789ab
DRILL_BUCKET=invenio-rdm-drill-example
python3 /config/s3-file-backup.py --config /config/config.json restore \
  --snapshot-id "$SNAPSHOT_ID" \
  --destination-bucket "$DRILL_BUCKET" \
  --destination-prefix "restore/$SNAPSHOT_ID/" \
  --confirm
```

Replace the snapshot ID and drill bucket with the approved verified values. The
command refuses a non-empty drill prefix, a missing bucket, or any target outside
`invenio-rdm-drill-*` and `restore/`.

Prune is disabled by default and is not scheduled. It requires a separate
destructive-scope approval, a strictly newer verified capture time, and a
destination-only delete identity:

```bash
python3 /config/s3-file-backup.py --config /config/config.json prune --confirm
```

The command preserves the latest verified point and same-time peers without a
strictly newer success; it never traverses source, CNPG or Velero prefixes.
Prune re-reads and hashes every object in complete points before
any deletion, validates that each point contains exactly its marker, manifest and
manifested objects, and refuses all deletion on incomplete, corrupt, unexpected or
unverifiable state. Inspectable incomplete, malformed or extra state returns
`attention` and CLI status 2; content-integrity, permission or network failures
return status 1. Restore is the only mode that writes outside the backup
destination. Do not add lifecycle expiry, a scheduled prune, or a broad storage
cleanup to replace the explicit mode. Provision the bucket, policies and Secret
privately using [the permission examples](s3-file-backup-permissions.md).

Limits: primary file bytes and retained backup points share the same MinIO and NFS
failure domain; no off-site copy exists. The nominal RPO is up to one day only while
jobs actually succeed. There is no atomic database/file snapshot, no resumable
multipart continuation, and no restore proof until the separately approved byte and
native-DB linkage drill completes. Parent #202 remains open until a real scheduled
success and isolated restore are verified.

### Retention change gate

Current retention is Velero `672h` (28d) for the weekly schedule, Prometheus and
Loki `7d`, and CNPG `30d`. Do not reduce CNPG to 7d until all of the following are
demonstrated, not inferred:

1. Barman can list a contiguous base-backup/WAL chain for the proposed 7d window.
2. A restore drill from that chain reaches a verified application/database state.
3. The file-store copy is independent and compatible with the chosen database
   recovery point; empty local PVC backups and canceled volume backups are not
   counted as coverage.
4. Barman dependency-aware cleanup removes expired bases only with their unneeded
   WAL, and the object store usage actually decreases.
5. The cleanup result is visible (deletion attempts/failures), and no manual or
   blanket bucket expiry is used.

Observed 2026-10-25: MinIO's export held about35GiB (CNPG backup data plus Velero);
Velero deletion metrics showed four attempts and four successes with zero failures.
CNPG had30d of backups created hourly by a five-field cron; this change corrects the
schedule to a six-field daily expression. Oldest recoverability point 2026-09-25, no
recent failed backup; 1,871 older failed Backup CRs from August remain as historical
API clutter with node-proxy 502 errors. These are status observations, not proof of
WAL integrity, restore success, or compatible file recovery.

The25October isolated drill recovered native backup `20261025T080202` and matched
production on80 public tables, schema digest `b6ee5d10de3e7659e4e0c5f64204240a`,
7 users,9,138 vocabularies and vocabulary digest `5fa4a963305262df6c7ad09cb88887e3`;
records/drafts/files were0 on both sides. The drill Cluster/namespace/Secret were
removed. This proves database recovery, not seeded file/record consistency. One
Released `Retain` PV remains for evidence; physical NFS cleanup is separate.

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
