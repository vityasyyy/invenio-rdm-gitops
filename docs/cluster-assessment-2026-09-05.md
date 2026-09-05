# Cluster Drift Assessment — 2026-09-05

Date: 2026-09-05
Context: `btd-rke2` (Rancher-managed RKE2, reachable only via university VPN)
Scope: lead-verified live-state snapshot for the docs-sync wave. Companion to
`docs/cluster-assessment-2026-09-02.md` (email-confirmation baseline) and
`docs/cluster-assessment-2026-08-14.md` (recovery baseline). All values below
are lead-verified live state — no cluster mutations were performed from this
docs-only sync (read-only `kubectl` per brief constraints).

## Summary

The cluster is at its healthiest baseline since the 2026-08-14 recovery: all 3
nodes Ready, **17 of 17 ArgoCD apps Synced+Healthy**, invenio web ×2 / worker
×2 / scheduler ×1 Running, ping 200, ArgoCD 200. Two major recoveries landed:
worker-02 kubelet streaming works again (university IT killed + restarted
worker-02, then reconnected the agent session), and postgres reconciled to
spec 1 / status 1 / ready 1 with Ready + ContinuousArchiving True. The
remaining red item is the **backup scheduler deadlock** (stuck since
2026-08-21, `already exists` error) — owned by the backup-diag worker, not
this sync. The machineID collision is unchanged, so flap risk remains.

## Cluster State

- Cluster context `btd-rke2`, API server reachable via the university VPN.
- Nodes: **3 nodes Ready** (control-plane + worker-01 + worker-02).
- Storage: NFS CSI (`btd-nfs` StorageClass, `nfs.csi.k8s.io`).
- Domains: `invenio.vityasy.me` (UI), `api-invenio.vityasy.me` (API) via
  Cloudflare Tunnel → Traefik IngressRoutes (ping 200).
- Image: `ghcr.io/vityasyyy/invenio-ugm` digest `0f685be` pinned in
  `k8s/apps/invenio/kustomization.yaml` and live in the cluster.

## ArgoCD Apps

| App | SYNC | HEALTH | Notes |
|---|---|---|---|
| apps (root app-of-apps) | Synced | Healthy | — |
| 16 application apps | Synced | Healthy | incl. invenio + invenio-bootstrap (scheduler fixed) |

**17 of 17 Synced+Healthy** (up from 16/17 on 2026-09-02 — the scheduler
CrashLoopBackOff is resolved).

## Workloads

- `invenio-web` ×2 Running, `invenio-worker` ×2 Running,
  `invenio-scheduler` ×1 Running.
- CNPG `postgres`: spec 1 / status 1 / ready 1, primary `postgres-3`
  (Ready True, ContinuousArchiving True, LastBackupSucceeded False).
  postgres-1/2 pods removed; their PVs are now Released.

## Findings

### (a) worker-02 streaming RESTORED — IT kill+restart reconnected the agent session — RESOLVED, flap risk remains

`kubectl logs` / `exec` / `proxy/healthz` to worker-02 pods all OK
(2026-09-05). Per the operator's root-cause note, university IT killed and
restarted worker-02, then reconnected the agent session — re-registering the
dropped remotedialer session that caused 90 days of 502s. **All 3 nodes still
share machineID `de88ca16...`** (VM clones), so the session can drop again on
the next reconnect flap. IT ticket for unique machine-ids still required.
(Plan: `docs/plans/active/2026-09-02-proxy-502.md`.)

### (b) Postgres reconciled to 1/1/1, Ready + Archiving True — RESOLVED (backup pipeline still red)

WAL move-aside unblocked the archiver; the operator removed postgres-1/2 and
`postgres-3` serves as the healthy single primary. `LastBackupSucceeded`
remains False: 1,872 failed Backup objects, zero completed.
(Plan: `docs/plans/active/2026-09-02-db-recovery.md`.)

### (c) Backup scheduler deadlock — stuck since 2026-08-21 — OWNED BY backup-diag WORKER

ScheduledBackup `postgres-daily-backup` has not advanced since
2026-08-21T02:02:00Z: every Backup CR creation fails with an `already exists`
error on `postgres-daily-backup-20260821030200`. Diagnosis + fix belong to the
backup-diag worker (`docs/plans/active/2026-09-05-backup-scheduler-deadlock.md`
— do not duplicate here).

### (d) Memory overcommit persists after zombie removal — NEW BASELINE, plan created

worker-02: **83% of requests / 354% of limits** (was 85%/360% at issue #74
filing — the zombie replicas are gone but limits overcommit is still far above
the 200% safety cap). worker-01: **67% of requests / 187% of limits**.
Right-size + rebalance plan created from issue #74:
`docs/plans/active/2026-09-05-worker-02-rebalance.md`.

### (e) SMTP still placeholder — unchanged, operator step pending

`MAIL_SERVER` is still `smtp.example.org`; sealed `MAIL_USERNAME` /
`MAIL_PASSWORD` still placeholders. (Plan:
`docs/plans/active/2026-09-02-email-confirmation.md`.)

### (f) 3 Released PVs — postgres-1, postgres-2, old redis-data — Task 16 scope

The postgres scale-down released two more PVs. Cleanup (PV delete + NFS admin
coordination for `/export/kube-btd/`) belongs to Task 16 of the
codebase-improvements plan.

### (g) Hygiene: sealed key single copy, `keys.txt` present

Sealed-secrets key pair exists at `~/.sealed-secrets/` (private + public pem)
with no backup found — still the highest-priority operator action. `keys.txt`
(stale MinIO creds, gitignored) still present in the main checkout.

## Concerns Requiring Follow-up

1. **Backup scheduler deadlock** (finding c) — backup-diag worker owns it.
2. **machineID collision flap risk** (finding a) — university IT ticket.
3. **Worker-02 overcommit 354% limits** (finding d) — issue #74 plan created.
4. **Sealed-secrets key single copy** (finding g) — back up now.
5. **SMTP credentials** (finding e) — operator supplies relay creds.
6. **Released PVs** (finding f) — Task 16 cleanup.
7. **Off-site backup + restore drills** — still pending (need completed backup first).

## Status

Healthiest baseline since recovery (17/17 Synced+Healthy). Streaming + postgres
reconcile resolved; backup scheduler deadlock is the active red item (other
worker); machineID, memory, SMTP, key-backup, and off-site remain queued.
