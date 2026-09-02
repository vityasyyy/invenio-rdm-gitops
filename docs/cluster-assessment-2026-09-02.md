# Cluster Drift Assessment — 2026-09-02

Date: 2026-09-02
Context: `btd-rke2` (Rancher-managed RKE2, reachable only via university VPN)
Scope: snapshot of cluster state for the email-confirmation wave (issue #67). Companion to `docs/cluster-assessment-2026-08-14.md` (recovery baseline).

## Summary

The cluster is back to a healthy baseline after the 2026-08-14 recovery: all 3
nodes Ready, 16 of 17 ArgoCD apps Synced+Healthy. One app (`invenio-bootstrap`)
is Progressing because the scheduler Deployment is crash-looping (finding (a),
fixed by #68). The main functional gap is **email confirmation is broken — no
SMTP configured anywhere** — which this wave fixes with placeholder plumbing
(issue #67). Several long-running infra concerns remain and are listed under
"Concerns requiring follow-up".

## Cluster State

- Cluster context `btd-rke2`, API server reachable via the university VPN.
- Nodes: **3 nodes Ready** (control-plane + worker-01 + worker-02, `btd-rke2`).
- Storage: NFS CSI (`btd-nfs` StorageClass, `nfs.csi.k8s.io`).
- Domains: `invenio.vityasy.me` (UI), `api-invenio.vityasy.me` (API) via
  Cloudflare Tunnel → Traefik IngressRoutes.

## ArgoCD Apps

| App | SYNC | HEALTH | Notes |
|---|---|---|---|
| apps (root app-of-apps) | Synced | Healthy | — |
| 15 application apps | Synced | Healthy | — |
| invenio-bootstrap | Synced | **Progressing** | scheduler Deployment CrashLoopBackOff (see finding (a)) |

**16 Synced+Healthy, 1 Progressing** (invenio-bootstrap, blocked on the
scheduler pod health).

## Findings

### (a) Scheduler CrashLoopBackOff — 3,617 restarts, `pgrep` missing — FIXED by #68

`invenio-scheduler` Deployment crash-loops (3,617 restarts) because the
container image lacks `pgrep`, which the scheduler's liveness/readiness probe
command requires. Fixed by PR #68 (probe rework). The app remains Progressing
until the fixed image rolls out and the pod goes Ready.

### (b) DB HA degraded — postgres-1 (437 restarts) + postgres-2 (360) crash-looping 18d — NEEDS FOLLOW-UP

Only `postgres-3` is healthy. `postgres-1` (437 restarts) and `postgres-2`
(360 restarts) have been crash-looping for ~18 days. The CNPG cluster is
functionally single-instance. **Needs follow-up**: investigate crash cause
(memory limits? NFS latency? CNPG replica bootstrap) and restore HA.

### (c) worker-02 memory overcommit — 88% requested / 360% limits

worker-02 runs at **88% of memory requests** and **360% of memory limits**
overcommit. Any pod that hits its limit risks OOM-kill cascades; scheduling
headroom is thin. Needs follow-up: right-size requests/limits or spread
workloads.

### (d) Image drift — `:latest` vs digest

The invenio-ugm image is referenced both as `:latest` (drift-prone) and pinned
by digest in `k8s/apps/invenio/kustomization.yaml`. Digest pinning is correct;
`:latest` references elsewhere should be eliminated to prevent silent drift.

### (e) Email confirmation broken — no SMTP — FIXED by this wave (#67)

Registration requires email confirmation (`SECURITY_CONFIRMABLE=True`,
`SECURITY_LOGIN_WITHOUT_CONFIRMATION=False`) but no mail server was configured:
no `MAIL_*` in `docker/ugm/invenio.cfg`, no mail vars in the ConfigMap, no
`MAIL_USERNAME`/`MAIL_PASSWORD` in the SealedSecret, no SMTP service in the
cluster. Flask-Mail fell back to `localhost:25` → confirmation emails never
sent → users could never log in. **Fixed by this wave**: env-driven `MAIL_*`
settings in `invenio.cfg`, placeholder relay in the ConfigMap, sealed placeholder
credentials in `invenio-app-secrets`. Operator must supply real university relay
credentials (see plan doc "Operator steps").

### (f) `keys.txt` — stale MinIO creds, gitignored

`keys.txt` (stale MinIO credentials) is gitignored but still present locally.
Not a live-cluster risk (MinIO retired for app files after the UGM migration),
but should be deleted to avoid confusion.

### (g) kubelet-proxy 502s on worker-02 exec — intermittent, node Ready

`kubectl exec`/`logs` to pods on worker-02 intermittently fail with
`proxy error ... 502` from the kubelet streaming proxy, even though the node is
Ready. Known pre-existing infra issue (also seen in the 2026-08-14 assessment);
intermittent rather than persistent. Needs follow-up.

## Concerns Requiring Follow-up

1. **DB HA** (finding b): postgres-1/2 crash-looping 18d — restore CNPG HA or
   document single-instance as accepted risk.
2. **worker-02 memory overcommit** (finding c): 88% requests / 360% limits —
   right-size or rebalance.
3. **Image drift** (finding d): eliminate `:latest` references; keep digest pins.
4. **kubelet-proxy 502s** (finding g): intermittent exec/log failures on
   worker-02 — investigate apiserver→kubelet streaming path.
5. **`keys.txt` cleanup** (finding f): remove stale gitignored credential file.
6. **SMTP credentials** (finding e): operator must supply university relay
   credentials and re-seal `MAIL_USERNAME`/`MAIL_PASSWORD` — the only remaining
   step for email confirmation to work end-to-end.

## Status

Baseline healthy (16/17 apps Synced+Healthy). Email confirmation plumbing
complete with placeholders (issue #67); awaiting operator credentials. Scheduler
fix (#68) in flight. DB HA and worker-02 memory overcommit are the top
follow-ups.
