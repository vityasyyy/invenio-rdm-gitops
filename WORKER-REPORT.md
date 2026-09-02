# WORKER-REPORT — Issue #75: Kubelet streaming-proxy 502 (T3)

**Worker:** fix/75-proxy-502 worktree
**Date:** 2026-09-03
**Status:** DIAGNOSIS COMPLETE — remediation commands prepared, **no cluster mutations performed** (docs-only commit)

## Root Cause (confirmed by reproduction)

The RKE2 supervisor kubelet proxy (`127.0.0.1:9345`) has **no remotedialer session
for `ubuntu-btd-kubernetes-worker-02`**. Reproduced the exact egress path from inside
`kube-apiserver-ubuntu-btd-kubernetes-server` (using the apiserver's own client cert):

```
CONNECT 10.17.117.42:10250 → HTTP/1.1 200 OK   (worker-01: tunnel OK)
CONNECT 10.17.117.41:10250 → HTTP/1.1 200 OK   (server node: tunnel OK)
CONNECT 10.17.117.43:10250 → HTTP/1.1 502 Bad Gateway
  {"message":"failed to find Session for client ubuntu-btd-kubernetes-worker-02","code":502}
```

This is the exact error string in every CNPG backup failure since 2026-06-04
(1,872 failed Backup objects). The kubelet on worker-02 is healthy: TCP+TLS
reachable from pod and host networks (401 = auth expected), cert valid to 2027-07-17.

## Evidence Summary (per boundary)

| Boundary | Finding |
|---|---|
| kubectl streaming | worker-01 logs/exec OK; worker-02 logs/exec 502 (all pods, incl. fleet-agent, CNPG operator) |
| kubelet direct | 10.17.117.43:10250 reachable (401) from cluster-agent pod, server kube-proxy, worker-01 kube-proxy |
| supervisor proxy :9345 | 200 for worker-01 + server; **502 "failed to find Session for client ubuntu-btd-kubernetes-worker-02"** for worker-02 |
| Rancher agents | cattle-cluster-agent v2.12.3 ×2 on server node (8–9 restarts, 41h ago); **no cattle-node-agent DaemonSet** (RKE2 uses host-level rancher-system-agent); agent logs show flapping websocket to 10.17.104.130:443 (connection refused / close 1006) |
| Nodes | All Ready=True, heartbeats fresh; worker-01 (15d old) shares machineID with worker-02 (VM clone) |
| CNPG | scheduledbackup 0 2 * * *, all backups failed since 2026-06-04; postgres-1/2 crash-looping on startup probe (separate issue) |

## Prepared Remediation (lead executes with user approval — NOT run)

1. `kubectl -n cattle-system rollout restart deploy/cattle-cluster-agent` + `rollout status` (forces session re-registration)
2. Verify: `kubectl -n invenio logs/exec invenio-web-f64d84446-mf5n6` ×3, `kubectl get --raw /api/v1/nodes/ubuntu-btd-kubernetes-worker-02/proxy/healthz`
3. If still 502: SSH to worker-02 → `systemctl restart rancher-system-agent` (host-level; needs user-supplied credentials) or Rancher UI Edit→Save on the node
4. Verify CNPG backup completes (`kubectl -n database get backups | tail` → `completed`)
5. Regression: worker-01 + server node streaming still OK

Full commands + escalation path in `docs/plans/active/2026-09-02-proxy-502.md`.

## What the Lead Must Approve

- [ ] `kubectl -n cattle-system rollout restart deploy/cattle-cluster-agent` (Step 1)
- [ ] SSH to worker-02 / Rancher UI node re-registration (Step 3, only if Step 1 fails)
- [ ] Manual CNPG backup trigger (Step 4)

## Files Changed (docs-only)

- `docs/plans/active/2026-09-02-proxy-502.md` (new — diagnosis + remediation plan)
- `docs/plans/README.md` (index entry)
- `WORKER-REPORT.md` (this file)

## Gaps / Notes

- No SSH access to nodes (all keys denied) — host-level agent checks deferred to lead/user.
- Rancher management API (`/v3/nodes`) not queryable with the cluster-scoped kubeconfig token (401) — Rancher UI access needed for deeper agent-state inspection.
- `postgres-1`/`postgres-2` startup-probe crash-loop (500 on /healthz) is a **separate** issue from the 502; flagged for a follow-up.
- worker-01/worker-02 duplicate `machineID` noted as a possible agent-identity risk if re-registration misbehaves.
