#!/usr/bin/env bash
# Validates monitoring dashboards (JSON + single folder + uid/title/tags),
# alert rules (severity/summary/runbook/dashboard/for annotations + Watchdog
# routed to Discord, never to null), the native-Discord receiver CR, and the
# absence of the deleted bridge stack. Exit non-zero on violation.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MON="${REPO_ROOT}/k8s/infra/monitoring"

python3 - "$MON" <<'PY'
import json, os, sys, yaml

mon = sys.argv[1]
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
        wd = [r for r in cr_route["routes"] if _matchers(r).get("alertname") == "Watchdog"]
        if not wd:
            err("no Watchdog route in CR spec.route.routes")
        elif wd[0].get("receiver") != "discord-warning":
            err(f"Watchdog routed to {wd[0].get('receiver')!r}, want 'discord-warning'")
        elif wd[0].get("repeatInterval") != "5m":
            err(f"Watchdog repeatInterval is {wd[0].get('repeatInterval')!r}, want '5m'")
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

for dead in ("alertmanager-discord-deployment.yaml",
             "alertmanager-discord-service.yaml",
             "alertmanager-discord-netpol.yaml"):
    if os.path.exists(os.path.join(mon, dead)):
        err(f"bridge file {dead} must be deleted")

kus = open(os.path.join(mon, "kustomization.yaml")).read()
if "discord-receivers.yaml" not in kus:
    err("kustomization.yaml must list discord-receivers.yaml")
for dead in ("alertmanager-discord-deployment.yaml",
             "alertmanager-discord-service.yaml",
             "alertmanager-discord-netpol.yaml"):
    if dead in kus:
        err(f"kustomization.yaml must not list {dead}")

if errors:
    print(f"\nFAILED: {len(errors)} violation(s)")
    sys.exit(1)
print(f"OK: {panels_seen} dashboards, {n_alerts} alerts, native-Discord routing valid")
PY
