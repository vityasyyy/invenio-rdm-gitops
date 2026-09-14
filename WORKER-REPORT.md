# WORKER-REPORT — wave-2 monitoring scrapes (#105) — IMPLEMENTED, awaiting lead live-proof

> Branch `feat/105-monitoring-scrapes`, 6 per-group commits on top of `a1bb66a`.
> All static gates green, negative case proven. No push (per instructions).
> Live `up==1` + sample-query proof per job needs VPN → lead.

## Per-group file list + evidence

### G1 — velero + cloudflared (commit `04492eb`)

- `k8s/infra/velero/velero-servicemonitor.yaml` (new) + kustomization entry.
- `external-lb/k8s/cloudflared-service.yaml`, `cloudflared-servicemonitor.yaml` (new) + kustomization entries.
- Chart keys probed: velero chart 11.4.0 (`helm pull`, inspection only) —
  `metrics.serviceMonitor.{enabled,autodetect,additionalLabels}` exist, but
  `autodetect: true` suppresses the object under `helm template` (CI could never
  prove it) → standalone manifest chosen deliberately.
- Render-grep hits (`rendered/k8s_infra_velero.yaml`):
  SM `velero/monitoring` labels `{release: monitoring}`, selector
  `{app.kubernetes.io/name+instance: velero}`, endpoint `http-monitoring`;
  `rendered/helm_velero.yaml` Service `velero` ports `[{http-monitoring, 8085}]`
  with exactly those labels → selector match proven.
- Cloudflared: DaemonSet args `--metrics 0.0.0.0:8080` (port proven);
  `/metrics` path per Cloudflare docs (Prometheus endpoint on the `--metrics`
  listener); Service `kube-system/cloudflared` port `metrics:8080` selector
  `{app: cloudflared}` matches DaemonSet pod labels (3 occurrences in manifest);
  SM selects it, `release: monitoring`.
- Label decision: Prometheus `serviceMonitorSelector {release: monitoring}`,
  `serviceMonitorNamespaceSelector {}` (from `rendered/helm_monitoring.yaml`
  Prometheus object) → SMs match in any namespace.
- DNS/label: job `kube-system/cloudflared` matches existing
  `CloudflareTunnelDown` regex `.*cloudflared.*` and `Tunnel up` panel — no
  alert/dashboard edit needed.

### G2 — opensearch (commit `44e9fe3`)

- Modified (kept in sync, 3 copies): `k8s/apps/invenio-deps/opensearch/values.yaml`,
  `argocd/apps/invenio-opensearch.yaml` valuesObject,
  `scripts/ci-render-manifests.sh` OPENSEARCH_VALUES.
- Diagnosis (pulled chart 2.32.0, AppVersion 2.19.1): NO `metricsExporter` key
  anywhere in values/templates — old block rendered nothing. Only top-level
  `serviceMonitor.{enabled,path,scheme,interval,labels}` + `plugins.{enabled,
  installList}`. Without the plugin, `/_prometheus/metrics` does not exist.
- Plugin pin (verified, not invented): tag `2.19.1.0` exists (exact OS match);
  asset `prometheus-exporter-2.19.1.0.zip` confirmed via GitHub API (149718 B).
  Note: upstream repo moved `aiven/…` → `opensearch-project/opensearch-prometheus-exporter`.
- Render-grep hits (`rendered/helm_opensearch.yaml`): SM
  `search/opensearch-cluster-master-service-monitor` labels include
  `release: monitoring`, selector `{app.kubernetes.io/name+instance: opensearch}`
  matches master Service, endpoint `http:9200 /_prometheus/metrics`;
  StatefulSet renders `opensearch-plugin install -b …2.19.1.0.zip`.
- Restarts the single OpenSearch node (plugin install) — off-peak merge.

### G3 — minio (commit `23c36e6`)

- Modified: `k8s/infra/minio/values.yaml`.
- Diagnosis (pulled chart 5.4.0, template read): SM renders ONLY if
  `metrics.serviceMonitor.includeNode: true` (was unset → only a Probe rendered);
  both objects carried `release: minio` while Prometheus selects
  `release: monitoring` on SM/Probe alike (`probeSelector` proven in render) →
  zero live series explained.
- Fix: `includeNode: true` (node SM `/minio/v2/metrics/node`, selector
  `{app+release+monitoring:"true"}` matches the Service), `interval/scrapeTimeout
  30s/10s`, `additionalLabels: {release: monitoring}` — duplicate-`release`-key
  render parses last-wins to `monitoring` (parse-proven).
  `public: true` (chart default) kept → no bearer auth, no 401 class.
- Render-grep hits (`rendered/helm_minio.yaml`): SM `minio` + Probe
  `minio-cluster` both labelled `release: monitoring`; SM endpoint
  `http:9000 /minio/v2/metrics/node`; Probe `minio.minio:9000
  /minio/v2/metrics/cluster`.

### G4 — traefik + postgresql (commit `11e4a20`)

- Modified: `k8s/infra/traefik/values.yaml`; `k8s/apps/invenio-deps/postgresql/cluster.yaml`;
  new `postgres-podmonitor.yaml` + kustomization entry.
- Traefik chart keys (39.0.6, `helm show values` + pulled-template read):
  `metrics.prometheus.{entryPoint,addEntryPointsLabels,addServicesLabels,
  service.enabled,disableAPICheck,serviceMonitor.{enabled,interval,
  scrapeTimeout,additionalLabels}}` — all verified present before use.
  Deployment already ran `--metrics.prometheus=true entrypoint=metrics` by chart
  default; our values add the dedicated `traefik-metrics:9100` Service + SM.
  `disableAPICheck: true` required — chart FAILs `helm template` (CI render)
  without CRDs otherwise; in-cluster CRDs exist.
- Render-grep hits (`rendered/helm_traefik.yaml`): Service `traefik-metrics`
  `:9100`; Deployment args include all three `--metrics.prometheus*` flags; SM
  `traefik/traefik` labelled `release: monitoring`, selector matches metrics
  Service, endpoint `targetPort: metrics path: /metrics`, `jobLabel: traefik`.
- PostgreSQL proof (chart evidence, negative): CNPG 0.23.0 Cluster CRD
  `monitoring` properties = `{enablePodMonitor, podMonitorMetricRelabelings,
  podMonitorRelabelings, customQueries*, disableDefaultQueries}` — NO
  `podMonitorLabels` field → operator object cannot carry `release: monitoring`.
  Upstream 1.25 monitoring.md (fetched): `enablePodMonitor` DEPRECATED,
  "manually create and manage a PodMonitor" with selector `cnpg.io/cluster:
  <name>`, metrics port `9187` named `metrics` (both quoted from docs).
  Fix: flag `false` (no double scrape) + manual PodMonitor
  (`rendered/k8s_apps_invenio-deps_postgresql.yaml`: `database/postgres`,
  `release: monitoring`, selector `cnpg.io/cluster: postgres`, port `metrics`).
- Rollout notes: traefik rolling restart (2 replicas), possible single-instance
  PG restart on flag change — off-peak merge.

### G5 — cleanup + routing + validator (commit `2438fdd`)

- Deleted `k8s/infra/monitoring/invenio-servicemonitor.yaml` + kustomization entry.
- Dead-panel check: NO dashboard panel references job `invenio-web`
  (only `job=` reference in all dashboards is `.*cloudflared.*`; every invenio
  panel is KSM/traefik-backed) → SM deletion only, no JSON edit.
- `values.yaml` inhibit_rules `source_match:/target_match_re:` →
  `source_matchers: [severity="critical"]` /
  `target_matchers: [severity=~"warning|info"]` (CR already used `matchers:`).
  `amtool v0.28.0 check-config` on stub-merged tree: SUCCESS (1 inhibit rule).
- Validator extended (`scripts/ci-validate-monitoring.sh`): invenio-SM absent
  (file + kustomization), matchers-only routing (anchored exact-key regex;
  `matchNames`/`matchLabels`/`matchers` safe), source-key pins for all six
  scrapes, stale-`metricsExporter` tripwires in all three opensearch copies.

### G6 — docs (this wave)

- `docs/plans/active/2026-09-14-monitoring-scrapes.md` (new) + `docs/plans/README.md` index row.
- This WORKER-REPORT.md (replaces the wave-1 report; history preserved in git).

## Full verification log (exact outputs, post-G5 unless noted)

```
$ yamllint argocd/ k8s/ external-lb/k8s/
(clean — only pre-existing line-length warnings)

$ bash scripts/ci-render-manifests.sh
Rendered: 19 manifests to rendered/
All renders succeeded

$ bash scripts/ci-validate-selectors.sh rendered
All selector validations passed

$ bash scripts/ci-validate-monitoring.sh
OK: 4 dashboards, 22 alerts, native-Discord routing valid

$ promtool check rules /tmp/rules-check.yaml   (spec.groups extraction)
Checking /tmp/rules-check.yaml
  SUCCESS: 22 rules found

$ python3 scripts/ci-stub-receivers.py && amtool check-config /tmp/am-merged-check.yaml
receivers: null, discord-critical, discord-warning
child routes: 3
Checking '/tmp/am-merged-check.yaml'  SUCCESS
(global, route, 1 inhibit rule, 3 receivers)

$ ClusterRole coverage (rendered/helm_monitoring.yaml)
monitoring-kube-prometheus-operator covers: [podmonitors, probes, servicemonitors]
(Prometheus role covers Services/endpoints/pods for scraping — correct split.)

$ git log --oneline (this branch)
2438fdd feat(monitoring): G5 drop invenio SM, matchers migration, validator pins (#105)
11e4a20 feat(db): G4 traefik metrics scrape + manual CNPG PodMonitor (#105)
23c36e6 fix(storage): G3 minio ServiceMonitor selection + node metrics (#105)
44e9fe3 feat(search): G2 fix opensearch exporter plugin + ServiceMonitor keys (#105)
04492eb feat(monitoring): G1 velero + cloudflared scrapes (#105)
```

Rule→dashboard mapping intact: no alert expr touched (only inhibit syntax +
validator changed); every touched alert's metric family has a scrape path now
(velero/minio/traefik/cnpg via new scrapes; KSM/AM self-scrapes unchanged).

## Negative-case log

Broke velero SM label (`release: monitoring` → `release: WRONG`):

```
$ bash scripts/ci-validate-monitoring.sh
✗ velero-servicemonitor.yaml must be a ServiceMonitor labelled release: monitoring

FAILED: 1 violation(s)
NEGATIVE_EXIT=1
```

After restore (`cp /tmp/velero-sm.bak …`):

```
OK: 4 dashboards, 22 alerts, native-Discord routing valid
RESTORE_EXIT=0
```

`git diff --stat k8s/infra/velero/velero-servicemonitor.yaml` after restore:
empty — only G1's intended content remains. Validator bites, restore is exact.

## BLOCKED/OPEN (needs VPN — lead)

1. Live proof per job (procedures + exact PromQL in the wave plan, "Lead
   live-proof procedures" table): `up{job}==1` + one sample query each for
   velero, cloudflared, opensearch, minio (SM + Probe), traefik, postgres;
   plus negative check that `up{job="monitoring/invenio-web"}` is ABSENT.
2. Grafana: previously-blank panels render (02 Data Velero/MinIO, 01 App
   OpenSearch/PG, 00 Overview traffic, 03 Platform Tunnel up).
3. Alerts evaluate (pending, not nodata-broken), esp. `VeleroBackupFailed/Stale`,
   `CNPGBackupStale/ArchivingDown`, `CloudflareTunnelDown`, `TraefikHigh404Rate`.
4. Rollout watches (off-peak merge): traefik 2-replica rollout, PG single-instance
   restart on flag change, opensearch single-node restart on plugin install;
   `/ping` 200 + ArgoCD all Synced+Healthy after sync.
5. kubeconform/kube-linter verdicts arrive via CI after push (`gh pr checks --watch`).
6. Deferred, not blocking: `opensearch_cluster_status` panel metric family —
   exporter serves it per plugin docs, but exact series names must be confirmed
   from the live `count by (__name__)` listing (procedure covers this); if the
   family name differs, panel expr needs a follow-up (new issue, not this wave).

## Deviations from issue #105 text (all evidenced above)

1. Velero via standalone SM manifest, not chart `metrics.serviceMonitor`
   (autodetect makes the chart path unprovable in CI render).
2. `enablePodMonitor: false` + manual PodMonitor instead of flag-on
   (CRD has no label field; upstream deprecates the flag) — needs lead sign-off.
3. No dead dashboard panel existed to delete (verified: no `invenio-web` job
   reference anywhere) — SM deletion only.
4. cloudflared-scrape awkwardness did NOT materialise (Service+SM, no DaemonSet
   edit) — no alert/panel drop needed, no sign-off required.

## Lead addendum (post-worker review, same branch)

- **OpenSearch egress (would-have-broken-search):** the exporter plugin installs
  from github.com in an initContainer on every pod creation, but namespace
  `search` default-denies egress with no HTTPS allow (DNS allow is correctly
  cross-namespace). Added `search-allow-egress-https` (opensearch pods →
  443/0.0.0.0/0) to `k8s/apps/invenio-deps/opensearch/manifests/network-policy.yaml`.
  Install step placement is initContainer (render lines ~173/219), so no
  reinstall-on-container-restart class. Gates re-green after the addition.
