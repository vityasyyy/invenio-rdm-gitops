# Cluster Drift Assessment — 2026-08-14

Date: 2026-08-14
Context: `btd-rke2` (Rancher-managed RKE2), API `https://10.17.104.130/k8s/clusters/c-m-vrpr6n7h`
Scope: baseline snapshot of cluster state after worker-01 lost its kubelet. Source of truth for the cluster recovery work (eviction + reconciliation in subsequent PRs).

## Summary

The cluster has been running in a degraded state since **2026-07-17T20:01:41Z**, when the kubelet on worker-01 stopped posting heartbeats. The machine itself is alive (ping OK, SSH port open) but is unreachable via SSH with all available keys, so it must be evicted and its 27 stuck Terminating pods force-deleted. With only worker-02 (8Gi) schedulable after eviction, the full baseline stack at original scale will not fit on one node; recovery means core data plane + application working at degraded scale.

## Cluster Access

- Cluster context `btd-rke2`, API server reachable via the university VPN (now connected).
- Nodes:

| Node | CPU | Memory | Status | Taint |
|---|---|---|---|---|
| `ubuntu-btd-kubernetes-server` (control-plane) | 8 CPU | 8Gi | Ready | `CriticalAddonsOnly` |
| `ubuntu-btd-kubernetes-worker-01` | 8 CPU | 8Gi | NotReady | — |
| `ubuntu-btd-kubernetes-worker-02` | 8 CPU | 8Gi | Ready | none |

## Root Cause

- worker-01 kubelet dead since **2026-07-17T20:01:41Z** (last heartbeat; lastTransition 20:06:02Z).
- Machine itself is ALIVE: ping OK (6ms), SSH port 22 open, kubelet port 10250 CONNECTION REFUSED, 6443 refused.
- No SSH access with available keys (root/ubuntu/miapalovaara + id_ed25519, id_ed25519_do all denied).
- Probable cause: memory over-commit. Before death, worker-01 Rancher annotation: pod-requests memory **7639Mi** (of ~8Gi), pod-limits **25301Mi** (3x over-commit). Redis OOM-looped then (and still does today: **3813 restarts, OOMKilled, exit 137**, on worker-02).
- 27 pods stuck Terminating on worker-01 (deletion timestamps set 2026-07-17T20:28+, never completed because kubelet is dead).

## Degraded State Evidence

| Component | State | Detail |
|---|---|---|
| argocd-application-controller (sts) | 0/1 | pod stuck Terminating on worker-01 → no ArgoCD reconciliation |
| CNPG cluster `postgres` (database ns) | Waiting for instances | primary=postgres-3; only postgres-1 pod exists (stuck Terminating, 675 restarts); postgres-2/3 pods absent |
| CNPG backups | failed | all backups since 2026-06-04 failed |
| OpenSearch (search ns) | 0/1 | opensearch-cluster-master-0 stuck Terminating on worker-01 |
| invenio-web deploy | 0/5 available | 4 pods Init:0/1 on worker-02 (wait-for-opensearch init loop), 2 stuck Terminating on worker-01, 1 Pending (insufficient memory) |
| invenio-worker deploy | 0/4 available | 4 Pending, 1 Init:0/1, 2 stuck Terminating |
| redis-master | Running but OOMKilled loop | 3813 restarts, exit 137, on worker-02 |
| traefik | 1 Running, 1 Pending | Pending: insufficient memory |
| monitoring/loki/velero | degraded | prometheus-0 stuck Terminating; velero pod Pending |
| PVCs | ALL Bound | postgres-1/2/3 (10Gi each, btd-nfs), opensearch (10Gi), prometheus (10Gi) — data survives |
| worker-02 | 100% memory-requested | requests 7890Mi / 8108328Ki capacity, 41 pods |
| ArgoCD apps | 6 not Healthy | invenio-bootstrap Degraded, invenio-postgresql Progressing, invenio-redis OutOfSync, loki/monitoring/monitoring-extras/traefik/velero/argocd-self Progressing |
| Public endpoints | invenio.vityasy.me 404 (Traefik), api-invenio 404, argocd.vityasy.me 200 | tunnel + traefik up, invenio IngressRoute present in git but no backend endpoints |

### Stuck pod list (all on worker-01, all Terminating)

| Namespace | Pod |
|---|---|
| argocd | argocd-application-controller-0 |
| argocd | argocd-dex-server-5d5754cf7d-4nljv |
| argocd | argocd-redis-55477f4cc6-ts6z5 |
| cattle-system | cattle-cluster-agent-64489565d8-q89sg |
| cert-manager | cert-manager-cainjector-58b478b58c-dgc4k |
| cert-manager | cert-manager-webhook-7db9c94796-w4drz |
| database | postgres-1 |
| invenio | invenio-web-dc65c4459-2g2q4 |
| invenio | invenio-web-dc65c4459-hsb2f |
| invenio | invenio-worker-77b6d66db4-79kj7 |
| invenio | invenio-worker-77b6d66db4-xg5pf |
| kube-system | cloudflared-62bwz |
| kube-system | csi-nfs-controller-57cc896bd6-5rdbw |
| kube-system | csi-nfs-node-rd584 |
| kube-system | kube-proxy-ubuntu-btd-kubernetes-worker-01 |
| kube-system | rke2-canal-gqphv |
| kube-system | rke2-coredns-rke2-coredns-65dc69968-bq2bg |
| kube-system | rke2-coredns-rke2-coredns-autoscaler-68d5f76f7-8vqf6 |
| kube-system | rke2-ingress-nginx-controller-d4jcn |
| kube-system | rke2-snapshot-controller-696989ffdd-p2sbm |
| kube-system | sealed-secrets-controller-c99454b98-9x94d |
| monitoring | alertmanager-discord-77bd74fcd7-bz628 |
| monitoring | loki-canary-jfpwm |
| monitoring | loki-chunks-cache-0 |
| monitoring | loki-results-cache-0 |
| monitoring | monitoring-prometheus-node-exporter-pkxqh |
| monitoring | prometheus-monitoring-kube-prometheus-prometheus-0 |
| search | opensearch-cluster-master-0 |
| velero | node-agent-dzt44 |

## Decisions (user-confirmed)

1. **Evict worker-01** (no access to machine): `kubectl delete node ubuntu-btd-kubernetes-worker-01`, then force-delete all 27 stuck Terminating pods on it.
2. **Keep control-plane taint** (`CriticalAddonsOnly`) — app pods do NOT go on the control-plane.
3. Consequence: only worker-02 (8Gi) schedulable → capacity is the hard constraint; expect to right-size (CNPG instances, invenio replicas) to fit. Full baseline stack at original scale will NOT fit on one node; recovery = core data plane + app working, degraded scale.
4. Fresh start is acceptable for data (no preservation requirement) — but PVCs survive anyway.
5. Baseline recovery FIRST, then UGM image migration (separate PRs).

## Impact

- No ArgoCD reconciliation while the application-controller pod is stuck Terminating; drift cannot be corrected until the node is evicted.
- Application completely down: invenio-web 0/5 available, invenio-worker 0/4 available; public endpoints invenio.vityasy.me and api-invenio return 404 (Traefik), argocd.vityasy.me returns 200.
- Database cluster `postgres` in `Waiting for instances` state; all CNPG backups failing since 2026-06-04.
- Redis in a perpetual OOMKilled restart loop (3813 restarts, exit 137).
- Data is NOT lost: all PVCs Bound (postgres-1/2/3 10Gi each btd-nfs, opensearch 10Gi, prometheus 10Gi).

## Recovery Plan

1. Evict worker-01 (`kubectl delete node ubuntu-btd-kubernetes-worker-01`) and force-delete the 27 stuck Terminating pods (PR in this branch's series).
2. Confirm worker-02 is the only schedulable node (control-plane taint kept).
3. Reconcile ArgoCD apps to baseline once the application-controller can run; right-size CNPG instances and invenio replicas to fit within worker-02's 8Gi.
4. Restore core data plane (postgres, opensearch, redis) and application working state at degraded scale.
5. Follow-up: UGM image migration in separate PRs.
