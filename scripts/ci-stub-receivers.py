#!/usr/bin/env python3
"""Build a merged Alertmanager config for `amtool check-config`.

Mirrors the operator merge (Branch A, matcher strategy None): the base root
route from k8s/infra/monitoring/values.yaml
(alertmanager.config.{global,route,inhibit_rules}, root-only, receiver null)
plus the discord-receivers AlertmanagerConfig CR's `spec.route` appended as a
first-level child route, with combined receivers. Each CR `discordConfigs[]`
entry becomes a native `discord_configs[]` entry with a dummy webhook_url
(schema-identical except the secret reference, which amtool cannot resolve
statically). Writes /tmp/am-merged-check.yaml and prints the receiver names.

camelCase -> snake_case mapping (CRD -> native):
  apiURL        -> webhook_url (replaced by dummy, secret ref not statically checkable)
  sendResolved  -> send_resolved
  title         -> title
  message       -> message
  groupBy/groupWait/groupInterval/repeatInterval -> group_by/group_wait/group_interval/repeat_interval
  matchers[{name, value}] -> match: {name: value} (equality; matchType empty)
  routes        -> routes (recursive)
"""
import copy
import os

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MON = os.path.join(REPO_ROOT, "k8s", "infra", "monitoring")
OUT = "/tmp/am-merged-check.yaml"
DUMMY_URL = "http://localhost:80/dummy"

ROUTE_KEYS = (
    ("receiver", "receiver"),
    ("groupBy", "group_by"),
    ("groupWait", "group_wait"),
    ("groupInterval", "group_interval"),
    ("repeatInterval", "repeat_interval"),
    ("continue", "continue"),
)


def convert_route(cr_route):
    native = {}
    for ck, nk in ROUTE_KEYS:
        if ck in cr_route:
            native[nk] = cr_route[ck]
    matchers = cr_route.get("matchers", [])
    if matchers:
        native["match"] = {m["name"]: m["value"] for m in matchers}
    if "routes" in cr_route:
        native["routes"] = [convert_route(r) for r in cr_route["routes"]]
    return native


values = yaml.safe_load(open(os.path.join(MON, "values.yaml")))
cfg = values["alertmanager"]["config"]
cr = yaml.safe_load(open(os.path.join(MON, "discord-receivers.yaml")))
crspec = cr.get("spec", {})

receivers = list(cfg.get("receivers", []))
for r in crspec.get("receivers", []):
    dcs = []
    for dc in r.get("discordConfigs", []):
        native = {"webhook_url": DUMMY_URL}  # apiURL secret ref -> dummy
        if "sendResolved" in dc:
            native["send_resolved"] = dc["sendResolved"]
        for f in ("title", "message"):
            if f in dc:
                native[f] = dc[f]
        dcs.append(native)
    receivers.append({"name": r["name"], "discord_configs": dcs})

root = copy.deepcopy(cfg["route"])
root["routes"] = [convert_route(crspec["route"])]

merged = {
    "global": cfg.get("global", {}),
    "route": root,
    "inhibit_rules": cfg.get("inhibit_rules", []),
    "receivers": receivers,
}
with open(OUT, "w") as f:
    yaml.safe_dump(merged, f)
print("receivers:", ", ".join(r["name"] for r in receivers))
print("child routes:", len(root["routes"][0].get("routes", [])))
print(f"wrote {OUT}")
