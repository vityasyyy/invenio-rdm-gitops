# Follow-up sync — monitoring scrape gaps #113, #115, #118

Follow-up to the closed monitoring waves #96 / #105 (plans in
`docs/plans/completed/`, index closed by #116). Three behavior PRs merged
since with no plan coverage; this doc closes that drift (issue #120).
Docs-only record — no manifest changes here.

## #113 — Wave-2 rollout gaps (commit `541fb11`, closes #112)

- **PodMonitor rename:** `postgres-podmonitor.yaml` →
  `postgres-metrics-podmonitor.yaml` (object `database/postgres` →
  `database/postgres-metrics`). Why: the old name collides with the
  CNPG-operator-owned object; `cluster.yaml` also drops the
  `monitoring.enablePodMonitor` stanza entirely (setting it kept the app
  `OutOfSync` — the live object carries no such field). Scraping stays with
  the manually managed PodMonitor.
- **Scrape ingress:** new `k8s/infra/velero/velero-scrape-netpol.yaml`
  (monitoring → velero `:8085`); `minio-allow.yaml` admits the monitoring
  namespace (`:9000`). Why: both namespaces otherwise default-deny ingress —
  velero scrapes failed live with `context deadline exceeded`.
- **Traefik SM:** new `k8s/infra/monitoring/traefik-servicemonitor.yaml`
  (`traefik-metrics`, selects `app.kubernetes.io/component: metrics` in ns
  `traefik`, `:9100/metrics`). Why: the chart's own ServiceMonitor selects
  the main `traefik` Service and drops every target; nothing selected the
  dedicated `traefik-metrics` Service.
- **Validator:** `scripts/ci-validate-monitoring.sh` pins all of the above
  (renamed PodMonitor + absence of old file, traefik SM selector +
  kustomization entry, velero netpol entry, minio monitoring allow).
- Files: `k8s/apps/invenio-deps/postgresql/{cluster,kustomization}.yaml`,
  PodMonitor rename, `k8s/infra/monitoring/{kustomization,traefik-servicemonitor}.yaml`,
  `k8s/infra/security/network-policies/minio-allow.yaml`,
  `k8s/infra/velero/{kustomization,velero-scrape-netpol.yaml}`,
  `scripts/ci-validate-monitoring.sh`. 9 files, +88/−13.

## #115 — PodMonitor RBAC (commit `6c02b74`, closes #114)

- One hunk: `k8s/infra/argocd/projects/invenio-project.yaml` whitelists
  `monitoring.coreos.com/ PodMonitor` in the invenio AppProject. Why: the
  manually managed postgres PodMonitor lives under the invenio project —
  without the whitelist ArgoCD refuses to sync it.

## #118 — Prometheus egress + traefik ingress (commit `5dcfab3`, closes #117)

- **Egress:** `monitoring-allow.yaml` `allow-prometheus-egress` gains ports
  `8085` (velero) and `9000` (minio). Why: missing ports fail closed with
  scrape timeouts (proven live 2026-09-15). Comment pins the rule: the port
  list must stay in sync with scraped targets.
- **Ingress:** new `network-policies/traefik-allow.yaml`
  (`allow-traefik-scrape-ingress`, monitoring → traefik `:9100`) + security
  kustomization entry. Why: ns `traefik` default-denies ingress — targets
  were discovered but scrapes timed out.
- **Validator:** asserts egress covers 8085/9000/9100 and the security
  kustomization lists `traefik-allow.yaml`.
- Files: `k8s/infra/security/{kustomization,network-policies/monitoring-allow.yaml,
  network-policies/traefik-allow.yaml}`, `scripts/ci-validate-monitoring.sh`.
  4 files, +44/−1.

## Live-sync status (2026-09-15, this worktree)

- **Not verified from here:** cluster reachable only via university VPN;
  `kubectl -n argocd get applications` fails (`dial tcp 10.17.104.130:443:
  i/o timeout`). ArgoCD selfHeal+prune should converge these manifest-only
  changes automatically; per-target `up==1` proof is lead/VPN work.
- Static side is covered: each PR extended `scripts/ci-validate-monitoring.sh`
  with pins for its own changes, and CI (`validate-infra.yaml`: yamllint +
  kustomize render + kubeconform + selector validation) gates every merge.

## Follow-ups

- #119 (open, owned by fix/119 worker): Prometheus infra targets down —
  host-network + missing ports blocked by egress. Out of scope here; that
  worker owns `k8s/` + `scripts/`, this branch owns docs only.
- Live `up{job}==1` per scrape target (velero `:8085`, minio `:9000`,
  traefik `:9100`, postgres PodMonitor) remains lead/VPN verification.
