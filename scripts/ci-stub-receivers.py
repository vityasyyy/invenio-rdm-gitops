#!/usr/bin/env python3
"""Build a merged Alertmanager config for `amtool check-config`.

Reads the base route tree from k8s/infra/monitoring/values.yaml
(alertmanager.config.{global,route,inhibit_rules}) and the native-Discord
receivers from k8s/infra/monitoring/discord-receivers.yaml, converting each
CR `discordConfigs[]` entry to a native `discord_configs[]` entry with a
dummy webhook_url (schema-identical except the secret reference, which
amtool cannot resolve statically). Writes /tmp/am-merged-check.yaml and
prints the receiver names.

camelCase -> snake_case mapping (CRD -> native):
  apiURL        -> webhook_url (replaced by dummy, secret ref not statically checkable)
  sendResolved  -> send_resolved
  title         -> title
  message       -> message
"""
import os
import sys

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MON = os.path.join(REPO_ROOT, "k8s", "infra", "monitoring")
OUT = "/tmp/am-merged-check.yaml"
DUMMY_URL = "http://localhost:80/dummy"

values = yaml.safe_load(open(os.path.join(MON, "values.yaml")))
cfg = values["alertmanager"]["config"]
cr = yaml.safe_load(open(os.path.join(MON, "discord-receivers.yaml")))

receivers = [{"name": "null"}]
for r in cr.get("spec", {}).get("receivers", []):
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

merged = {
    "global": cfg.get("global", {}),
    "route": cfg["route"],
    "inhibit_rules": cfg.get("inhibit_rules", []),
    "receivers": receivers,
}
with open(OUT, "w") as f:
    yaml.safe_dump(merged, f)
print("receivers:", ", ".join(r["name"] for r in receivers))
print(f"wrote {OUT}")
