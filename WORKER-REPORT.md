# WORKER-REPORT — monitoring overhaul (#96) — COMPLETE (T1–T6, awaiting lead/T7)

> Status: T1–T6 committed on `feat/96-monitoring-overhaul`, all static gates
> green, negative case proven. Branch left unpushed for lead verification.
> Live delivery proof (pipecheck first run, Discord test delivery, Grafana
> render check) needs VPN → T7/lead.

## Per-task file list

- **T1** (commit `701f197`): created `scripts/ci-validate-monitoring.sh`,
  appended `validate-monitoring` job to `.github/workflows/validate-infra.yaml`.
- **T2** (commit `54a6e75`): rewrote `k8s/infra/monitoring/grafana-dashboards.yaml`
  (5 ConfigMaps → 4, all `grafana_folder: InvenioRDM`).
- **T3** (commit `d72bcd5`): rewrote `k8s/infra/monitoring/alerts.yaml`
  (5 groups `app-slo`, `data-backups`, `capacity`, `edge`, `platform-self`, 22 rules).
- **BLOCKER-1 fix** (commit `95a4a4f`, lead-approved): CI promtool step now
  extract-then-check; validator reports clean `discord-receivers.yaml missing`
  violation instead of traceback.
- **T4** (commit `e218d85`): deleted `alertmanager-discord-deployment.yaml`,
  `alertmanager-discord-service.yaml`, `alertmanager-discord-netpol.yaml`;
  created `k8s/infra/monitoring/discord-receivers.yaml` (AlertmanagerConfig CR)
  and `scripts/ci-stub-receivers.py`; modified `values.yaml` (route tree +
  `alertmanagerConfigSelector: {}`) and `kustomization.yaml`; validator updated
  for CRD `apiURL` spelling + CR-receiver route allowance (see deviations).
- **T5** (commit `e9eddf9`): created `k8s/infra/monitoring/monitoring-pipecheck.yaml`,
  registered in `kustomization.yaml`.
- **T6** (this report + spec status flip, commit pending): full gates green,
  negative case proven (below).

## T1 validator initial FAIL output (verbatim)

```
✗ ConfigMap grafana-dashboard-cluster-health: grafana_folder is 'Kubernetes', want 'InvenioRDM'
✗ grafana-dashboard-cluster-health/cluster-health.json: title 'Cluster Health Overview' must start with 00/01/02/03
✗ ConfigMap grafana-dashboard-traefik: grafana_folder is 'Traefik', want 'InvenioRDM'
✗ grafana-dashboard-traefik/traefik-traffic-errors.json: title 'Traefik Traffic & Errors' must start with 00/01/02/03
✗ ConfigMap grafana-dashboard-velero: grafana_folder is 'Velero', want 'InvenioRDM'
✗ grafana-dashboard-velero/velero-backups.json: title 'Velero Backups' must start with 00/01/02/03
✗ ConfigMap grafana-dashboard-minio: grafana_folder is 'MinIO', want 'InvenioRDM'
✗ grafana-dashboard-minio/minio-capacity-availability.json: title 'MinIO Capacity & Availability' must start with 00/01/02/03
✗ ConfigMap grafana-dashboard-invenio: grafana_folder is 'Invenio', want 'InvenioRDM'
✗ grafana-dashboard-invenio/invenio-operations.json: title 'Invenio Operations' must start with 00/01/02/03
✗ want exactly 4 dashboards, found 5
✗ alert PodCrashLooping: missing annotation 'description' (+ runbook_url, dashboard)
... (all old alerts missing runbook_url/dashboard)
✗ Watchdog routed to 'null', want 'discord-warning'
✗ alertmanager config has no inhibit_rules
FileNotFoundError: .../k8s/infra/monitoring/discord-receivers.yaml   (CR did not exist yet;
  since fixed: clean `discord-receivers.yaml missing` violation, exit stays non-zero)
EXIT=1
```

## T2 proofs + decisions

KSM-RBAC proof (`bash scripts/ci-render-manifests.sh` exit 0, then greps on
`rendered/helm_monitoring.yaml`):

- `grep -c persistentvolumes` → **5** (nonzero) → kept Released-PVs panel.
- `grep -c '"pods"'` → **1** (nonzero) → kept OOM-kills panel
  (`kube_pod_container_status_terminated_reason`).
- Extra (same render, for the T2 pipecheck panel): ClusterRole covers `jobs`
  (line 958 + `--resources=...jobs...` flag line 2056) → kept
  `kube_job_status_succeeded` panel.
- AM self-scrape (for T3 E-group): `grep -c alertmanager_notifications` → **7**;
  `grep -c alertmanager_config_last_reload_successful` → **1**. Both kept.
- KSM scrape job name in render is exactly `job="kube-state-metrics"`
  (so T3 `KubeStateMetricsDown` regex `.*kube-state-metrics.*` matches).
- Rendered Service names: Prometheus `monitoring-kube-prometheus-prometheus`,
  Alertmanager **`monitoring-kube-prometheus-alertmanager`**
  (plan guessed `monitoring-kube-alertmanager` — T5 uses the rendered name,
  lead-approved).
- CNPG Cluster namespace confirmed `database`
  (`k8s/apps/invenio-deps/postgresql/cluster.yaml`) → overview "DB pods Running"
  uses `namespace="database"`.
- Overview `links` → `/d/invenio-app`, `/d/data-backups`, `/d/platform-health`.
- Dropped panels (no thresholds / superseded): "Core Targets Up", "Unhealthy
  Pods" (covered by "Targets down"), "Invenio Proxy Traffic & Errors"
  (duplicated RPS/error per catalogue), "Invenio PVC Usage (%)" (moved to
  overview "Max PVC usage %"), "Velero Targets Up", "MinIO Targets Up"
  (covered by platform "Targets down").
- Demoted `InvenioTraffic4xxRatioHigh` alert → overview "Invenio 4xx %" stat panel.

## T3 CNPG freshness proof

- Docs URL (operator chart `cloudnative-pg 0.23.0` = CNPG ~1.25):
  `https://raw.githubusercontent.com/cloudnative-pg/cloudnative-pg/release-1.25/docs/src/monitoring.md`
  fetched OK (no fallback needed).
- Proven metric names (verbatim from that version's docs):
  - `cnpg_collector_last_available_backup_timestamp` ("The last available
    backup as a unix timestamp") → `CNPGBackupStale`:
    `cnpg_collector_last_available_backup_timestamp > 0 and (time() - ...) > 26*3600`,
    warning, `for: 1h`. The `> 0` guard added because the docs state the
    metric "will be zero until your first backup to the object store".
  - `cnpg_collector_pg_wal_archive_status{value="ready"|"done"}` ("Number of
    WAL segments in the archive_status directory") → `CNPGArchivingDown`:
    `cnpg_collector_pg_wal_archive_status{value="ready"} > 5`, warning,
    `for: 15m`. DEVIATION (lead-accepted): the spec's `ContinuousArchiving==False`
    is a CR status condition, not a Prometheus metric in this CNPG version; the
    ready-file backlog is the version-documented Prometheus-native equivalent.
- No deferrals — both freshness alerts implemented.
- Other T3 decisions (lead-accepted, all committed):
  - `VeleroBackupFailed` keeps its name but its expr changed from the generic
    `velero_backup_attempt_total - velero_backup_success_total > 0` (which the
    plan orders deleted) to `sum(increase(velero_backup_failure_total[1h])) > 0`.
  - Renamed `InvenioTraffic5xxRatioHigh` → `Invenio5xxRatioHigh` per catalogue.
  - Deleted with no replacement rule (dashboard panels cover them):
    `PodNotReady`, `MinIOHighDiskUsage`, `OpenSearchClusterRed`,
    `InvenioTraffic4xxRatioHigh` (demoted to panel), generic attempt-minus-success.
  - `InvenioPodRestartHigh` widened per catalogue (`>5/1h`, `for: 15m`).
  - `PodCrashLooping` (rate>0) replaced by `PodCrashLoopingWithDown` (critical,
    `for: 15m`).
  - Rule→dashboard mapping: A→`invenio-app` (except `TraefikServiceDown`→overview);
    B→`data-backups`; C→`platform-health` (except `InvenioPVCUsageHigh`→overview);
    D→`overview-invenio-rdm`; E→`platform-health`.

## T4 chart findings (Step 1 — all pass)

Pulled `prometheus-community/kube-prometheus-stack --version 69.6.0`
(inspection only; note: pulled layout nests CRDs at
`charts/crds/crds/crd-alertmanagerconfigs.yaml`, and Alertmanager has no
separate subchart dir — image lives in top-level `values.yaml`):

- Bundled Alertmanager image tag: **`v0.28.0`** (≥v0.25 → native
  `discord_configs` available). amtool used below is exactly v0.28.0.
- Selector key path: **`alertmanager.alertmanagerSpec.alertmanagerConfigSelector`**
  (`values.yaml:885 alertmanagerSpec:` → `:939 alertmanagerConfigSelector: {}`).
  Enabled in our `values.yaml` as `alertmanagerConfigSelector: {}` (selects all
  AlertmanagerConfigs, chart default semantics).
- CRD `discordConfigs` support: nonzero (`grep -c` → 1; block at CRD line 228).
- CRD `inhibitRules` support: nonzero (`grep -c` → 1; block at CRD line 56).
- DEVIATION (plan-anticipated, CRD-spelling class): the CRD's `discordConfigs[]`
  items use **`apiURL`** (`{name, key}` SecretKeySelector — "The secret's key
  that contains the Discord webhook URL"), NOT `webhookUrl`. The only
  `webhookUrl` in the CRD (2 hits, line 1970) belongs to **msteamsConfigs**
  ("MSTeams webhook URL", required there). Our CR therefore uses
  `apiURL: {name: alertmanager-discord-webhook, key: DISCORD_WEBHOOK_URL}`
  (key verified against the sealed secret; secret is in namespace `monitoring`,
  same as the CR, as the CRD requires). `sendResolved`/`title`/`message`
  spellings match the CRD verbatim. Consequential validator + stub-script
  updates: validator checks `apiURL` keyRef; stub maps `apiURL→webhook_url`
  (dummy). No fallback to the pinned-bridge design needed.
- T4 Step 6 gate: `amtool check-config` on the stub-merged config → SUCCESS
  (3 receivers, 1 inhibit rule); validator → `OK: 4 dashboards, 22 alerts,
  native-Discord routing valid`, exit 0.
- T4 Step 7: `ci-render-manifests.sh` monitoring lines
  (`✓ kustomize: k8s/infra/monitoring`, `✓ helm: monitoring`, 19 manifests);
  staged set was exactly the 3 deletions + CR + script + values + kustomization.

## T5 pipecheck

- Service DNS verified against render (both names present, multiple hits):
  `monitoring-kube-prometheus-prometheus:9090`,
  `monitoring-kube-alertmanager` replaced by rendered
  `monitoring-kube-prometheus-alertmanager:9093` (lead-approved).
- Curl image (crane-resolved, no `latest` pin):
  **`curlimages/curl:8.22.0@sha256:58adaa4e8dca9c988bae2aba4ab3434a0bb2da16bbe3f92dec39ec7785166777`**
  (`latest` and `8.22.0` digests identical; config label confirms 8.22.0).
- `ci-render-manifests.sh` → "All renders succeeded";
  `ci-validate-selectors.sh rendered` → "All selector validations passed".
- T7 note (pre-recorded): if the Job fails on the securityContext on VPN, the
  documented first fix is dropping `runAsUser`/`runAsGroup`/`fsGroup`.

## T6 full verification output (Step 1 — every command exit 0)

```
yamllint argocd/ k8s/ external-lb/k8s/   → clean (line-length warnings only, pre-existing style)
bash scripts/ci-render-manifests.sh      → Rendered: 19 manifests / All renders succeeded
bash scripts/ci-validate-selectors.sh rendered → All selector validations passed
promtool check rules /tmp/rules-check.yaml (spec.groups extraction, lead-approved)
                                         → SUCCESS: 22 rules found
python3 scripts/ci-stub-receivers.py     → receivers: null, discord-critical, discord-warning
amtool check-config /tmp/am-merged-check.yaml → SUCCESS (global, route, 1 inhibit rule, 3 receivers)
bash scripts/ci-validate-monitoring.sh   → OK: 4 dashboards, 22 alerts, native-Discord routing valid
```

(kubeconform/kube-linter run in CI after lead pushes — not run here.)

## T6 negative-case proof (Step 2)

Broke the first rule (removed `runbook_url` from `InvenioWebReplicasUnavailable`):

```
✗ alert InvenioWebReplicasUnavailable: missing annotation 'runbook_url'

FAILED: 1 violation(s)
exit=1
```

After restore (`cp /tmp/alerts.bak ...`):

```
OK: 4 dashboards, 22 alerts, native-Discord routing valid
```

`git diff --stat k8s/infra/monitoring/alerts.yaml` after restore: empty —
only T3's intended changes remain. The validator bites and the restore is exact.

## Deviations / fixes summary (all recorded; BLOCKER-1 class lead-approved)

1. **BLOCKER-1 (fixed, lead-approved):** CI promtool step is extract-then-check
   (promtool cannot parse the PrometheusRule CR wrapper); `--ignore-unknown-fields`
   rejected (vacuous 0-rule pass). Validator missing-CR crash → clean violation.
2. **BLOCKER-2 (fixed in T4 commit, needs lead sign-off on the diff):**
   the verbatim T1 validator flagged routes pointing at the CR receivers as
   "unknown" — yet the design (and T4 Step 6 expectation `OK / exit 0`) requires
   exactly that. amtool v0.28.0 on the merged config proves the routing correct
   (SUCCESS), so the validator now allows route receivers present in either the
   base config or the CR (`receivers | cr_receivers | {None}`), while still
   enforcing base receivers = `[null]` only. Without this, `OK / exit 0` is
   unreachable by construction.
3. CRD spelling: CR + validator + stub use `apiURL`, not `webhookUrl`
   (plan-anticipated deviation class; `webhookUrl` in this CRD is MSTeams-only).
4. T2–T3 content deviations (lead-accepted): CNPG archiving expr, VeleroBackupFailed
   expr, renames/deletions, rendered AM DNS name.
5. Environment adaptations (no plan impact): no sudo on this host → promtool
   v3.13.3 / amtool v0.28.0 / crane v0.22.1 installed to `~/.local/bin`
   (darwin-arm64 builds; CI installs its own linux binaries per the workflow);
   `helm pull` layout paths adapted (CRDs under `charts/crds/crds/`).

## BLOCKED/OPEN (needs VPN — T7/lead, unchanged)

- pipecheck first run (`kubectl -n monitoring get jobs`; securityContext fallback noted above).
- Discord test delivery (`MonitoringPipeTest` arrival + resolve; first
  `[CRITICAL]`-style title check; `Watchdog` pulse within 10 min of sync).
- Grafana render check (one `InvenioRDM` folder, 4 dashboards, each opened once).
- Confirm no `alertmanager-discord` pods remain anywhere.
- kubeconform/kube-linter verdicts arrive via CI after push (`gh pr checks --watch`).
