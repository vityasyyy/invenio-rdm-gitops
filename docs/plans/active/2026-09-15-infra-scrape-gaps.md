# Infra scrape gaps — Prometheus egress for host-network targets (#119)

Status: fix implemented in worktree, all static gates green, awaiting lead live-proof (no VPN in worker session).

## Problem

Live 2026-09-15: 17/34 Prometheus targets `down` with `context deadline exceeded`.
All wave-2 app scrapes `up`; all infra jobs dark: kubelet x9 (3 nodes x
/metrics + /metrics/cadvisor + /metrics/probes on 10.17.117.41/42/43:10250),
node-exporter x2 (10.17.117.42/43:9100), coredns x2 (pod IPs :9153),
kube-controller-manager :10257, kube-scheduler :10259, kube-etcd :2381
(all 10.17.117.41), operator :10250 (pod IP 10.42.2.54).

## Root cause (verified statically — see Evidence)

`allow-prometheus-egress` (monitoring ns) had two gaps:

1. **Missing ports**: 9153 / 10250 / 10257 / 10259 / 2381 absent. Any missing
   port fails closed with scrape timeouts (same failure mode as #117/#118).
2. **namespaceSelector can never match host IPs**: kubelet, node-exporter
   (hostNetwork DaemonSet — render-proven `hostNetwork: True`), and the
   control-plane/etcd targets live on node host IPs 10.17.117.x, not pod IPs.
   The existing stanza (`namespaceSelector: {}`) only matches pod IPs.

Apiserver stayed `up` because `allow-monitoring-kube-api` opens 443/6443 to
all destinations. Egress policies are additive (union), so the fix is purely
additive too — no existing allow is narrowed.

## Fix

`k8s/infra/security/network-policies/monitoring-allow.yaml`:

- Pod-target ports added to the namespaceSelector stanza: **9153** (coredns),
  **10250** (operator pod; also covers future pod targets).
- New least-privilege ipBlock stanza: **10.17.117.0/24** with ports
  9100 / 10250 / 10257 / 10259 / 2381 (node-exporter, kubelet,
  controller-manager, scheduler, etcd).
- Deliberately NOT added: kube-proxy :10249 (no live targets observed —
  add only on evidence).

No target-side ingress changes: kube-system has no default-deny ingress;
monitoring-internal already admits monitoring→monitoring (covers the operator
pod); hostNetwork pods / host processes are outside NetworkPolicy ingress
scope. Lead to confirm with `kubectl get networkpolicy -A` (cluster may have
drifted; worker had no VPN).

`scripts/ci-validate-monitoring.sh`: port pins extended to
8085/9000/9100/9153/10250/10257/10259/2381 + node-subnet CIDR pin.

## Evidence (worker session, offline — cluster unreachable, VPN down)

- Rendered `helm_monitoring.yaml` (chart 69.6.0): coredns Service :9153,
  controller :10257, scheduler :10259, etcd :2381, node-exporter
  hostNetwork=True :9100, operator Deployment containerPort 10250 (Service
  443→https), kubelet SM 3 https-metrics endpoints (/, /metrics/cadvisor,
  /metrics/probes). Every down target's port is now in the egress allow.
- Negative case: breaking the 10257/CIDR pins fails the validator with 2
  violations; restore is byte-exact (`diff` clean).
- Full gates green (exact outputs in WORKER-REPORT.md).

## Lead live-proof procedures (needs VPN)

1. `kubectl -n monitoring get networkpolicy allow-prometheus-egress -o yaml`
   — confirm ArgoCD synced the new stanza.
2. Prometheus `/api/v1/targets`: all 17 previously-down targets `up`.
3. PromQL: `up{job=~".*kubelet.*"}==1`, `up{job=~".*node-exporter.*"}==1`,
   `up{job=~".*coredns.*"}==1`, controller/scheduler/etcd/operator `up==1`.
4. Grafana node/kubelet panels render (were No Data).
5. `kubectl get networkpolicy -A` — confirm no other default-deny blocks
   the targets; confirm node subnet is 10.17.117.0/24 (fix assumes /24 from
   observed .41/.42/.43 — narrow to /32s or widen if the VPC CIDR differs).

## Open questions (recorded, not guessed)

1. **Traefik ServiceMonitor duplicate** (see WORKER-REPORT.md §4): the chart
   SM (`traefik/traefik`, selector now matches ONLY the metrics Service) and
   our standalone SM (`monitoring/traefik-metrics`) select the same Service.
   Static evidence + keep-one recommendation recorded; live target count
   (`traefik-metrics x2 up` vs replicas=2) is ambiguous without cluster
   access — lead to decide before any deletion.
2. **Node subnet breadth**: /24 assumed from observed IPs. If the node VPC
   CIDR is larger/smaller, adjust the ipBlock (validator pins the CIDR, so
   any change must update both files together).
