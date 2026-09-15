# Monitor noise cleanup — unreachable scrapes + heartbeat routing (#123)

Follow-up to #119/#122 (infra egress fix, merged). Live 2026-09-15 post-merge:
31/34 targets `up` (kubelet x9, coredns x2, node-exporter x2, operator all
recovered; KubeletDown resolved). Three jobs still `down`, plus heartbeat
alerts reaching Discord.

## Problem 1 — control-plane scrapes can never work

`kube-controller-manager :10257`, `kube-scheduler :10259`, `kube-etcd :2381`
fail with `connection refused` (was `context deadline exceeded` before #122
— the egress fix worked, packets now reach the host). RKE2 binds these
daemons to localhost, so no NetworkPolicy can ever fix them. Their TargetDown
warnings would spam Discord forever. The matching alert rules were already
disabled in values.yaml (`etcd/kubeScheduler*/kubeControllerManager: false`);
the monitors were left on by oversight.

Fix: `kubeEtcd/kubeScheduler/kubeControllerManager.enabled: false` in
`k8s/infra/monitoring/values.yaml` + validator pins. Re-enable if the control
plane ever exposes these ports off-localhost.

## Problem 2 — Watchdog/InfoInhibitor reach Discord

Both carry severity `none`, matching neither tier sub-route — but the CR root
route has no matchers, so it catches everything first and they land in
discord-warning (InfoInhibitor reached Discord 3x live 2026-09-15). The #111
fall-through-to-base-null theory was wrong.

Fix: empty `blackhole` receiver in `discord-receivers.yaml` (base already owns
`null`; duplicates are invalid) + explicit blackhole routes for
`alertname=Watchdog|InfoInhibitor` (first, before tier routes) + validator
pins (receivers exactly critical+warning+blackhole; blackhole stays empty).

## Not in scope (recorded)

- invenio-worker CPU saturation (HPA 186%/70% at maxReplicas 2, worker
  throttled ~35%, request 500m/limit 1000m): real capacity signal, needs a
  sizing decision (raise limit vs maxReplicas 2→3; nodes have 5-8% real CPU
  use) — separate issue, operator call.
- postgres CPUThrottlingHigh `pending`: watch only.
- minio mc-inspect KubeContainerWaiting `pending`: one-shot/hook leftover,
  watch only.
- KubeMemoryQuotaOvercommit: known #74 residual posture.
- Traefik SM duplicate question (from #119 plan): still open, needs live
  targets-page proof.

## Lead live-proof procedures (needs VPN)

1. `up{job=~"kube-controller-manager|kube-scheduler|kube-etcd"}` ABSENT
   (targets gone, not just `up==0`).
2. `ALERTS{alertname=~"TargetDown"}`: no series for those 3 jobs.
3. Alertmanager: Watchdog + InfoInhibitor received by `blackhole` (silence expected;
   confirm via Alertmanager UI/API, not Discord absence alone).
4. Grafana node/kubelet panels render (the #119 payoff).
5. ArgoCD all Synced+Healthy; Deploy Verify green.
