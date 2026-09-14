#!/usr/bin/env bash
# Validates monitoring dashboards (JSON + single folder + uid/title/tags),
# alert rules (severity/summary/runbook/dashboard/for annotations), the
# native-Discord receiver CR (Watchdog must NOT route to Discord, issue #110;
# messages must guard empty annotations), and the absence of the deleted
# bridge stack. Issue #105 adds: invenio-SM absence, matchers-only routing,
# and source-key pins for every new scrape (velero, cloudflared, minio,
# opensearch, traefik, postgres PodMonitor).
# Exit non-zero on violation.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MON="${REPO_ROOT}/k8s/infra/monitoring"

python3 - "$MON" <<'PY'
import json, os, sys, yaml

mon = sys.argv[1]
REPO_ROOT = mon.rstrip("/").removesuffix("/k8s/infra/monitoring")
errors = []
def err(msg):
    errors.append(msg)
    print(f"✗ {msg}")

dash_path = os.path.join(mon, "grafana-dashboards.yaml")
docs = [d for d in yaml.safe_load_all(open(dash_path)) if d]
panels_seen = 0
for cm in docs:
    meta = cm.get("metadata", {})
    folder = ((meta.get("annotations") or {}).get("grafana_folder"))
    if folder != "InvenioRDM":
        err(f"ConfigMap {meta.get('name')}: grafana_folder is {folder!r}, want 'InvenioRDM'")
    for key, blob in (cm.get("data") or {}).items():
        try:
            d = json.loads(blob)
        except json.JSONDecodeError as e:
            err(f"{meta.get('name')}/{key}: invalid JSON: {e}")
            continue
        panels_seen += 1
        for field in ("uid", "title", "tags"):
            if field not in d:
                err(f"{meta.get('name')}/{key}: missing {field!r}")
        if d.get("title", "")[:2] not in ("00", "01", "02", "03"):
            err(f"{meta.get('name')}/{key}: title {d.get('title')!r} must start with 00/01/02/03")
if panels_seen != 4:
    err(f"want exactly 4 dashboards, found {panels_seen}")

rules = yaml.safe_load(open(os.path.join(mon, "alerts.yaml")))["spec"]["groups"]
n_alerts = 0
for group in rules:
    for rule in group.get("rules", []):
        if "alert" not in rule:
            continue
        n_alerts += 1
        name = rule["alert"]
        labels = rule.get("labels", {})
        ann = rule.get("annotations", {})
        if labels.get("severity") not in ("critical", "warning"):
            err(f"alert {name}: severity must be critical|warning")
        for field in ("summary", "description", "runbook_url", "dashboard"):
            if not ann.get(field):
                err(f"alert {name}: missing annotation {field!r}")
        if "for" not in rule:
            err(f"alert {name}: missing anti-flap 'for:'")
if n_alerts == 0:
    err("no alert rules found")

values = yaml.safe_load(open(os.path.join(mon, "values.yaml")))
try:
    cr = yaml.safe_load(open(os.path.join(mon, "discord-receivers.yaml")))
except FileNotFoundError:
    err("discord-receivers.yaml missing")
    cr = None
if cr is None:
    cr_receivers = set()
elif cr.get("kind") != "AlertmanagerConfig":
    err("discord-receivers.yaml kind must be AlertmanagerConfig")
    cr = None
    cr_receivers = set()
else:
    cr_receivers = {r.get("name") for r in cr.get("spec", {}).get("receivers", [])}
route = values["alertmanager"]["config"]["route"]
receivers = {r.get("name") for r in values["alertmanager"]["config"].get("receivers", [])}
# Base Secret must be valid standalone: root-only route, base receivers only.
if route.get("receiver") != "null":
    err(f"base root receiver is {route.get('receiver')!r}, want 'null'")
if route.get("routes"):
    err("base route must be root-only — tier sub-routes live in the AlertmanagerConfig CR")
if "discord-critical" in receivers or "discord-warning" in receivers:
    err("base receivers must be [null] only — discord receivers live in the AlertmanagerConfig CR")
if not values["alertmanager"]["config"].get("inhibit_rules"):
    err("alertmanager config has no inhibit_rules")
# Matcher strategy None keeps the CR route cluster-wide (default OnNamespace
# would gate it to namespace=monitoring and drop cross-namespace alerts).
strat = ((values.get("alertmanager") or {}).get("alertmanagerSpec") or {}).get("alertmanagerConfigMatcherStrategy") or {}
if strat.get("type") != "None":
    err(f"alertmanagerConfigMatcherStrategy.type is {strat.get('type')!r}, want 'None'")

def _matchers(route):
    return {m.get("name"): m.get("value") for m in route.get("matchers", [])}

if cr is not None:
    crspec = cr.get("spec", {})
    if cr_receivers != {"discord-critical", "discord-warning"}:
        err(f"CR receivers are {cr_receivers}, want exactly discord-critical + discord-warning")
    cr_route = crspec.get("route") or {}
    if not cr_route.get("routes"):
        err("CR spec.route must carry the tier sub-routes")
    else:
        # No heartbeat-to-human by design (issue #110): a Watchdog route to any
        # Discord receiver reintroduces channel spam. Silence is covered by
        # NotificationsFailing, the pipecheck hook, and up-based target alerts.
        wd = [r for r in cr_route["routes"] if _matchers(r).get("alertname") == "Watchdog"]
        if wd:
            err(f"Watchdog must not route to Discord (found receiver {wd[0].get('receiver')!r})")
        for r in cr_route["routes"]:
            if r.get("receiver") not in cr_receivers:
                err(f"CR route references unknown receiver {r.get('receiver')!r}")
    for r in crspec.get("receivers", []):
        dcs = r.get("discordConfigs", [])
        if not dcs:
            err(f"CR receiver {r.get('name')}: no discordConfigs")
        for dc in dcs:
            url = (dc.get("apiURL") or {})
            if url.get("name") != "alertmanager-discord-webhook" or not url.get("key"):
                err(f"CR receiver {r.get('name')}: apiURL must keyRef the sealed alertmanager-discord-webhook Secret")
            # Guarded annotations: chart-default alerts carry no runbook_url /
            # dashboard, so unguarded lines render empty (issue #110).
            if "{{ with .Annotations" not in str(dc.get("message", "")):
                err(f"CR receiver {r.get('name')}: message must guard annotations with {{{{ with }}}}")

for dead in ("alertmanager-discord-deployment.yaml",
             "alertmanager-discord-service.yaml",
             "alertmanager-discord-netpol.yaml"):
    if os.path.exists(os.path.join(mon, dead)):
        err(f"bridge file {dead} must be deleted")

kus = open(os.path.join(mon, "kustomization.yaml")).read()
if "discord-receivers.yaml" not in kus:
    err("kustomization.yaml must list discord-receivers.yaml")
if "alertmanager-egress-netpol.yaml" not in kus:
    err("kustomization.yaml must list alertmanager-egress-netpol.yaml")
if "pipecheck-egress-netpol.yaml" not in kus:
    err("kustomization.yaml must list pipecheck-egress-netpol.yaml")
for dead in ("alertmanager-discord-deployment.yaml",
             "alertmanager-discord-service.yaml",
             "alertmanager-discord-netpol.yaml"):
    if dead in kus:
        err(f"kustomization.yaml must not list {dead}")

grafana_flag = values.get("grafana", {}).get("defaultDashboardsEnabled", True)
if grafana_flag is not False:
    err("grafana.defaultDashboardsEnabled must be false (drop chart-bundled dashboard noise)")

# Issue #105: no old-style Alertmanager routing keys remain (match:/match_re: in
# routes, source/target_match(_re): in inhibit rules). Anchored exact-key match
# so matchNames/matchLabels/matchers never trip it.
import re
_old_route_key = re.compile(r"(?m)^\s*(match|match_re|source_match|source_match_re|target_match|target_match_re)\s*:")
for fname in ("values.yaml", "discord-receivers.yaml"):
    body = open(os.path.join(mon, fname)).read()
    for m in _old_route_key.finditer(body):
        err(f"{fname}: old-style routing key {m.group(1)!r} — use matchers: (issue #105)")

# Issue #105: invenio-web ServiceMonitor is deleted (endpoint serves nothing).
if os.path.exists(os.path.join(mon, "invenio-servicemonitor.yaml")):
    err("invenio-servicemonitor.yaml must be deleted (endpoint serves nothing)")
if "invenio-servicemonitor.yaml" in kus:
    err("kustomization.yaml must not list invenio-servicemonitor.yaml")

# Issue #105: every new scrape is wired in source (render-grep proof lives in
# WORKER-REPORT.md; these assertions pin the source keys so regressions fail CI).
def _load(rel):
    with open(os.path.join(REPO_ROOT, rel)) as fh:
        return yaml.safe_load(fh)

vel = _load("k8s/infra/velero/velero-servicemonitor.yaml")
if vel.get("kind") != "ServiceMonitor" or (vel.get("metadata", {}).get("labels") or {}).get("release") != "monitoring":
    err("velero-servicemonitor.yaml must be a ServiceMonitor labelled release: monitoring")
elif vel["spec"]["endpoints"][0].get("port") != "http-monitoring":
    err("velero ServiceMonitor must scrape port http-monitoring (:8085)")

cf_svc = _load("external-lb/k8s/cloudflared-service.yaml")
cf_sm = _load("external-lb/k8s/cloudflared-servicemonitor.yaml")
if [p.get("port") for p in cf_svc["spec"].get("ports", [])] != [8080]:
    err("cloudflared Service must expose port 8080")
if cf_sm.get("kind") != "ServiceMonitor" or (cf_sm.get("metadata", {}).get("labels") or {}).get("release") != "monitoring":
    err("cloudflared-servicemonitor.yaml must be a ServiceMonitor labelled release: monitoring")
elif cf_sm["spec"]["endpoints"][0].get("port") != "metrics":
    err("cloudflared ServiceMonitor must scrape port name metrics")

mino = _load("k8s/infra/minio/values.yaml")["metrics"]["serviceMonitor"]
if mino.get("includeNode") is not True:
    err("minio metrics.serviceMonitor.includeNode must be true (else only an unselected Probe renders)")
if (mino.get("additionalLabels") or {}).get("release") != "monitoring":
    err("minio metrics.serviceMonitor.additionalLabels.release must be monitoring")

for rel in ("k8s/apps/invenio-deps/opensearch/values.yaml",):
    osv = _load(rel)
    if "metricsExporter" in osv:
        err(f"{rel}: stale metricsExporter key (chart 2.32.0 has no such key)")
    if not osv.get("serviceMonitor", {}).get("enabled"):
        err(f"{rel}: serviceMonitor.enabled must be true")
    if (osv.get("serviceMonitor", {}).get("labels") or {}).get("release") != "monitoring":
        err(f"{rel}: serviceMonitor.labels.release must be monitoring")
    if not (osv.get("plugins", {}).get("installList") or []):
        err(f"{rel}: plugins.installList must carry the verified exporter zip")
argo_os = open(os.path.join(REPO_ROOT, "argocd/apps/invenio-opensearch.yaml")).read()
if "metricsExporter" in argo_os:
    err("argocd/apps/invenio-opensearch.yaml: stale metricsExporter key")
if "serviceMonitor:" not in argo_os or "prometheus-exporter-2.19.1.0.zip" not in argo_os:
    err("argocd/apps/invenio-opensearch.yaml: must carry serviceMonitor + verified exporter zip")
render_sh = open(os.path.join(REPO_ROOT, "scripts/ci-render-manifests.sh")).read()
if "metricsExporter" in render_sh:
    err("scripts/ci-render-manifests.sh: stale metricsExporter key in OPENSEARCH_VALUES")
if "prometheus-exporter-2.19.1.0.zip" not in render_sh:
    err("scripts/ci-render-manifests.sh: OPENSEARCH_VALUES must carry the verified exporter zip")

trm = _load("k8s/infra/traefik/values.yaml")["metrics"]["prometheus"]
if (trm.get("service") or {}).get("enabled") is not True:
    err("traefik metrics.prometheus.service.enabled must be true")
if (trm.get("serviceMonitor") or {}).get("enabled") is not True:
    err("traefik metrics.prometheus.serviceMonitor.enabled must be true")
if ((trm.get("serviceMonitor") or {}).get("additionalLabels") or {}).get("release") != "monitoring":
    err("traefik serviceMonitor.additionalLabels.release must be monitoring")

pgc_docs = list(yaml.safe_load_all(open(os.path.join(REPO_ROOT, "k8s/apps/invenio-deps/postgresql/cluster.yaml"))))
pgc = next(d for d in pgc_docs if d and d.get("kind") == "Cluster")
if (pgc["spec"].get("monitoring") or {}).get("enablePodMonitor") is not False:
    err("postgresql cluster.yaml monitoring.enablePodMonitor must be false (manual PodMonitor owns the scrape)")
pgm = _load("k8s/apps/invenio-deps/postgresql/postgres-podmonitor.yaml")
if pgm.get("kind") != "PodMonitor" or (pgm.get("metadata", {}).get("labels") or {}).get("release") != "monitoring":
    err("postgres-podmonitor.yaml must be a PodMonitor labelled release: monitoring")
elif (pgm["spec"].get("selector", {}).get("matchLabels") or {}).get("cnpg.io/cluster") != "postgres":
    err("postgres PodMonitor must select matchLabels cnpg.io/cluster: postgres")

if errors:
    print(f"\nFAILED: {len(errors)} violation(s)")
    sys.exit(1)
print(f"OK: {panels_seen} dashboards, {n_alerts} alerts, native-Discord routing valid")
PY
