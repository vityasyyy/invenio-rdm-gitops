# WORKER-REPORT — monitoring overhaul (#96) — PARTIAL (T1–T3 committed, BLOCKED)

> Status: T1, T2, T3 committed on `feat/96-monitoring-overhaul`. T4–T6 NOT
> started — blocked on the promtool-vs-CR question in BLOCKED/OPEN below.
> This report will be extended as work resumes.

## Per-task file list

- **T1** (commit `701f197`): created `scripts/ci-validate-monitoring.sh`,
  appended `validate-monitoring` job to `.github/workflows/validate-infra.yaml`.
- **T2** (commit `54a6e75`): rewrote `k8s/infra/monitoring/grafana-dashboards.yaml`
  (5 ConfigMaps → 4, all `grafana_folder: InvenioRDM`).
- **T3** (commit `d72bcd5`): rewrote `k8s/infra/monitoring/alerts.yaml`
  (5 groups `app-slo`, `data-backups`, `capacity`, `edge`, `platform-self`, 22 rules).
- T4–T6: pending (bridge files still present, no CR yet, no pipecheck yet).

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
... (all old alerts missing runbook_url/dashboard; full list in shell history)
✗ Watchdog routed to 'null', want 'discord-warning'
✗ alertmanager config has no inhibit_rules
FileNotFoundError: .../k8s/infra/monitoring/discord-receivers.yaml   (CR does not exist yet)
EXIT=1
```

Note: the verbatim T1 script crashes with `FileNotFoundError` when
`discord-receivers.yaml` is absent instead of reporting a clean violation.
Still exit non-zero as expected; T4 will create the file. No script change
made (plan content kept verbatim).

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
- Rendered Service names (for T5 — differ from plan guess):
  Prometheus `monitoring-kube-prometheus-prometheus` (matches plan),
  Alertmanager **`monitoring-kube-prometheus-alertmanager`**
  (plan guessed `monitoring-kube-alertmanager` — T5 must use the rendered name).
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
- T2 Step 3 check: dashboard violations gone; only alert/CR/routing/bridge
  violations remained. T3 Step 4 check: alert violations gone; only
  `Watchdog routed to 'null'` + `no inhibit_rules` + missing-CR
  `FileNotFoundError` remain (all T4 scope).

## T3 CNPG freshness proof (Step 1)

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
    `for: 15m`. DEVIATION NOTE: the spec's `ContinuousArchiving==False` is a
    CR status condition, not a Prometheus metric in this CNPG version; the
    ready-file backlog is the version-documented Prometheus-native equivalent.
- No deferrals — both freshness alerts implemented.
- Other T3 decisions (recorded, all committed):
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
  - `KubeStateMetricsDown` regex verified against render (`job="kube-state-metrics"`).
- promtool on extracted rules: `SUCCESS: 22 rules found` (procedure below).

## Chart findings (T4 — not started)

Pending: bundled AM tag, CRD `discordConfigs`/`inhibitRules` support,
`alertmanagerConfigSelector` key path.

## Curl image (T5 — not started)

Pending: tag + digest.

## Verification log (so far)

- `yamllint .github/workflows/validate-infra.yaml` → exit 0 (line-length
  warnings only, matching pre-existing style) after adding the missing trailing
  newline the append dropped.
- `bash scripts/ci-render-manifests.sh` → exit 0.
- `bash scripts/ci-validate-monitoring.sh` after T3 → only T4-scope violations
  (see T2 section above).
- promtool (installed v3.13.3 darwin-arm64 to `~/.local/bin`, no sudo on this
  host; CI still installs its own per the committed job): extracted-rules check
  green; literal plan command red — see BLOCKED/OPEN.
- Negative case (T6 Step 2): not run yet.

## BLOCKED/OPEN

### BLOCKER-1 (plan bug, stops T4–T6): `promtool check rules` cannot parse the PrometheusRule CR

- Exact command (T3 Step 3 / T6 Step 1 / committed T1 CI step /
  acceptance criteria):
  `promtool check rules k8s/infra/monitoring/alerts.yaml`
- Exact output (promtool v3.13.3):
  ```
  Checking k8s/infra/monitoring/alerts.yaml
    FAILED:
  k8s/infra/monitoring/alerts.yaml: yaml: unmarshal errors:
    line 1: field apiVersion not found in type rulefmt.RuleGroups
    line 2: field kind not found in type rulefmt.RuleGroups
    line 3: field metadata not found in type rulefmt.RuleGroups
    line 6: field spec not found in type rulefmt.RuleGroups
  ```
- Diagnosis: `promtool check rules` only accepts raw rule files
  (`{groups: [...]}` at top level). Our file must stay a
  `monitoring.coreos.com/v1 PrometheusRule` CR (the cluster consumes it via
  kustomize), so the literal command can never succeed — the plan step, the
  committed T1 CI step, and the acceptance criterion contradict the required
  file shape. No plan fallback covers this.
- Ruled out: `promtool check rules --ignore-unknown-fields` exits 0 but reports
  `SUCCESS: 0 rules found` — a vacuous pass that would let broken PromQL through.
  Rejected as a dishonest gate; do not use.
- Proven: extracting `spec.groups` and checking the extract gives
  `SUCCESS: 22 rules found` (5 groups / 22 alerts, matching the committed file).
- Proposed fix (needs lead approval): change the CI step
  "Check PrometheusRule syntax" to extract-then-check:
  ```yaml
        - name: Check PrometheusRule syntax
          run: |
            python3 -c "import yaml; d=yaml.safe_load(open('k8s/infra/monitoring/alerts.yaml')); yaml.safe_dump({'groups': d['spec']['groups']}, open('/tmp/rules-check.yaml','w'))"
            promtool check rules /tmp/rules-check.yaml
  ```
  and mirror it in T6 Step 1. Everything else in T1–T3 stays as-is.
- Committed green: T1 (`701f197`), T2 (`54a6e75`), T3 (`d72bcd5`) — validator
  alert-half green, rules PromQL-valid per extraction check.

### OPEN (needs VPN, for T7/lead — unchanged)

- pipecheck first run, Discord test delivery, Grafana render check.
