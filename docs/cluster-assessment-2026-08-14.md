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

---

# Recovery: single-node right-sizing

Date: 2026-08-15
Reference: PR #54 (merged to main as `20ce6d9`), task #53. App-of-apps tracks `main`; no targetRevision overrides.

## Right-sizing changes (applied to git, in main)

- `k8s/apps/invenio/invenio-hpa.yaml`
  - `invenio-web-hpa`: minReplicas 2 -> **1**, maxReplicas 5 -> **2**
  - `invenio-worker-hpa`: minReplicas 2 -> **1**, maxReplicas 4 -> **2**
- `k8s/apps/invenio-deps/postgresql/cluster.yaml`
  - `spec.instances`: 3 -> **1** (single primary; replicas re-added when nodes return)

Rationale: with worker-01 evicted, only worker-02 (8Gi) is schedulable. The full baseline
stack (web 5 + worker 4 + postgres 3 + everything else) over-commits the node (requests
7890Mi / 8108Mi = 99%) and cannot schedule. Right-sizing cuts web/worker HPA targets and
postgres instances so the core path fits on one node.

## Sync / application state

| App | targetRevision | SYNC | HEALTH | Notes |
|---|---|---|---|---|
| apps | main | Synced | Healthy | root app-of-apps, revision 20ce6d9 |
| invenio-bootstrap | main | **OutOfSync** | **Degraded** | op Running, blocked "waiting for healthy state of apps/Deployment/invenio-web" |
| invenio-postgresql | main | Synced | Healthy | CNPG operator chart |
| invenio-redis | main | Synced | Progressing | revision 20ce6d9 |

The bootstrap sync is wave-ordered: it applied the new web Deployment (wave 3) but will not
apply the HPA right-sizing (wave 4) until web becomes healthy. Web cannot become healthy
because redis is down, and redis cannot start because its dataset exceeds its memory limit
(see below). **The HPA right-sizing has therefore NOT reached the live cluster** — live HPA
is still web min 2 / max 5 and worker min 2 / max 4.

## Pod state (worker-02 only)

| Component | State |
|---|---|
| redis-master | CrashLoopBackOff, **134 restarts**, all OOMKilled (exit 137) |
| postgres-3 | Ready, primary, 1/1 |
| postgres-1 / postgres-2 | Running 0/1 (CNPG operator recreated them; cluster status still `instances: 3`, phase "Waiting for the instances to become active") |
| invenio-web | 2 new-rev Running (startup-probe failing, exit 137 after ~300s), 1 old-rev OOM-looping, rest Pending |
| invenio-worker | all Pending (insufficient memory) |
| opensearch | 1/1 Ready |

## Redis OOM root cause (evidence)

- Node is NOT memory-starved: actual usage 5641Mi / 8108Mi = 71%; node conditions all OK.
- redis container limit = **512Mi**, request = 128Mi; args `redis-server --appendonly yes`; no `maxmemory` set.
- redis is OOM-killed ~15-19s after container start, at ~383Mi and climbing (observed via
  `kubectl top`); exit 137 `OOMKilled` -> **cgroup limit hit, not node pressure**.
- The redis PVC (`redis-data`, 5Gi NFS) holds **~548Mi** of data:
  - `appendonlydir/appendonly.aof.15.base.rdb` = 190Mi
  - `appendonlydir/appendonly.aof.15.incr.aof` = 112Mi
  - `dump.rdb` = 193Mi
  - `temp-7644.rdb` = 66Mi (stale temp from interrupted save)
- At startup redis replays the base RDB + incremental AOF into memory; the loaded dataset
  plus redis overhead exceeds the 512Mi limit before AOF rewrite can complete.

Conclusion: freeing node memory does not help redis — the fix requires a manifest change
(raise the memory limit, e.g. to 1Gi, and/or set `maxmemory` with an eviction policy, and/or
clean the stale `temp-*.rdb`). Per task constraints this is **reported, not changed**, in this
session.

## Deadlock chain (why the right-sizing cannot self-heal yet)

1. redis OOM-loops (dataset 548Mi > 512Mi limit) -> redis down
2. invenio-web cannot initialize (cache/broker connection) -> web never Ready, /ping never served
3. ArgoCD bootstrap sync waits for web health before applying HPA wave 4 -> HPA stays min 2/max 5
4. Node requests stay ~99% -> new pods (worker, extra web) stay Pending
5. Loop persists until redis is fixed or HPA is applied out-of-band

## Additional infra findings

- **kubelet streaming proxy broken for worker-02**: `kubectl logs`/`exec` to any pod on
  worker-02 returns `proxy error from 127.0.0.1:9345 while dialing 10.17.117.43:10250, code 502`
  (server-node pods work). Node status, metrics, and kubelet `:10250` healthz all OK, so the
  apiserver -> worker kubelet streaming path is broken. This also breaks CNPG backups
  (`LastBackupFailed: error dialing backend ... 502`).
- CNPG cluster condition: `ClusterIsNotReady`, `ContinuousArchiving=False`.

## Endpoint codes (2026-08-15)

| Endpoint | Code |
|---|---|
| https://invenio.vityasy.me/ping | **404** |
| https://api-invenio.vityasy.me/api | **404** |
| https://argocd.vityasy.me | **200** |

## Node memory (worker-02)

- requests 7890Mi / allocatable 8108Mi = **99%** (pod-requests annotation)
- actual usage ~5641Mi = 71% (`kubectl top`)

## Status

Recovery is **incomplete / blocked on redis**. The right-sizing is correct in main and will
apply once web can become healthy; the redis 512Mi limit vs 548Mi dataset is the hard blocker
and needs a manifest decision (raise limit / set maxmemory / trim stale RDB). See
`.superpowers/sdd/2026-08-14-cluster-recovery-and-ugm-migration/task-3-report.md`.
