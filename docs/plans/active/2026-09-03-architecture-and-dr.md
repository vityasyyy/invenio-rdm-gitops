# Architecture Deep-Dive, Failure Modes & DR Findings — Implementation Plan

> **Date:** 2026-09-03
> **Context:** User requested a full understanding of the deployment (architecture, components, data flow, failure modes, DR posture) and asked for findings + concerns to be documented. Companion to `docs/cluster-assessment-2026-09-02.md`.

## Why

The deployment is a **pilot** but should be "properly deployed" per the operator. To recover from disaster we must understand: what runs, how data flows, where the single points of failure are, and what is (and isn't) backed up. This plan records that understanding and the remediation roadmap.

## What InvenioRDM is

- Open-source **research data repository** platform, built by CERN (the software behind Zenodo).
- Researchers **deposit datasets + metadata** → get persistent URLs/DOIs → **publish records** → **search/discover** → **download files**.
- Not one process: **web (uWSGI/Flask) + worker (Celery) + scheduler (Celery beat)** share one codebase (the UGM image).

## What DBRepo is (and is NOT)

Per the planning ledger (`docs/planning-ledger.md`):

> **DBRepo-UGM is a SEPARATE product (do not conflate).** DBRepo is TU Wien's Database Repository (v1.13.4) — a docker-compose stack (MariaDB/Postgres, Keycloak, RabbitMQ, OpenSearch, SeaweedFS, metadata/consumer/search/data/dashboard services, forked Nuxt/Vuetify UI). **It is NOT Invenio.** Shipping as compose + nginx gateway; not Kubernetes-native.

| Aspect | InvenioRDM (this repo) | DBRepo |
|---|---|---|
| Product | Research **data** repository (CERN/Zenodo lineage) | **Database** repository (TU Wien) |
| Stack | Python Flask + Celery | JVM services + Nuxt UI |
| Deploy mode | Kubernetes (ArgoCD) | docker-compose + nginx |
| In this repo? | ✅ `docker/ugm/` | ❌ new separate ArgoCD app if ever |
| Status | Deployed, running (pilot) | **Decision pending — user was only curious; no plan to deploy** |

## Architecture (how it works)

### Traffic path — zero inbound ports

```
Internet → Cloudflare Edge (TLS/DDoS/WAF) → Cloudflare Tunnel (outbound websocket)
         → Traefik Ingress (hostname routing) → invenio-web (uWSGI)
```

The cluster opens **no inbound firewall ports**; `cloudflared` DaemonSet (kube-system) holds an outbound connection to Cloudflare. Public traffic in via tunnel; admin (kubectl) only via university VPN to `https://10.17.104.130` (Rancher).

### Data plane

```
invenio-web / worker / scheduler
   ├── PostgreSQL (CNPG, database ns)   ← SOURCE OF TRUTH (records, users, metadata)
   ├── Redis (redis ns)                 ← Celery broker/backend, sessions, cache, ratelimit
   ├── OpenSearch (search ns)           ← derived search index (rebuildable from Postgres)
   └── MinIO (minio ns)                 ← dataset FILES (S3 object storage)
```

**Data flow mental model:**
1. **Postgres = source of truth**; everything else is derived or transient.
2. **OpenSearch = derived index**; losing it loses search, not data — reindex from Postgres.
3. **MinIO = files**; record metadata in Postgres references object keys.
4. **Redis = ephemeral**; losing it re-queues tasks / drops sessions / clears cache.
5. **Scheduler → Redis → worker** = periodic tasks (session cleanup, file integrity, scheduled jobs).

### GitOps control plane

```
push main → CI (yamllint/kustomize/kubeconform/gitleaks) → ArgoCD app-of-apps (16 apps)
         → auto-sync (waves: security -5 → traefik -4 → sealed-secrets -3 → tunnel -1
           → storage 1-2 → observability 5-6 → deps 7 → invenio 9)
         → SealedSecrets controller decrypts (key OUTSIDE repo) → cluster converges
```

### Image pipeline

`docker/ugm/` = UGM instance layer (Dockerfile + invenio.cfg). CI `build-image.yaml` builds `ghcr.io/vityasyyy/invenio-ugm` on `docker/ugm/**` changes; kustomization pins digest; ArgoCD rolls.

## Failure modes (observed + theoretical)

| Failure | Impact | Status 2026-09-03 |
|---|---|---|
| Node dies (worker-01 July) | Pods stuck Terminating; degraded | Happened; recovered; **no HA (2 workers)** |
| Postgres primary dies | **App down** (source of truth) | **Single-instance (postgres-3) — #1 risk** |
| CNPG backups fail | No DB recovery path | **90d broken** (502, exec-based) |
| Scheduler dies | Periodic tasks silently stop | Happened 13d; **fixed** (probe, #70) |
| Redis OOM | Queue/cache/session loss | Happened July (3,813 restarts); healthy now |
| OpenSearch dies | Search down; app degraded | Rebuildable from Postgres |
| MinIO dies | Files unreachable; metadata survives | Healthy |
| Sealed-secrets key lost | **All secrets unrecoverable** | **Single copy in `~/.sealed-secrets/` — NO backup found** |
| machineID collision (all 3 nodes) | Rancher agent identity chaos → 502 | **Active; blocks CNPG backups + kubectl exec on worker-02** |
| Memory overcommit | OOM-kill cascades | worker-02 83% requests / 354% limits (post-zombie) |
| Velero backup fails | No infra recovery | ✅ **Running** (last 2026-08-30) — not affected by 502 (kopia/node-agent, fs-level) |

## Findings (2026-09-03, verified)

1. 🔴 **CNPG backups broken 90d** (1,872 failed CRs) — root cause #75 (worker-02 session missing, machineID collision).
2. 🔴 **Single-instance Postgres** — no HA; accepted for pilot IF backups work; else untenable.
3. 🔴 **machineID collision**: all 3 nodes share `de88ca16...` (VM clones never regenerated `/etc/machine-id`). Needs **VM console / university IT** (not SSH — all keys denied). Rancher UI edit-save at node and cluster level did **not** re-register worker-02's session.
4. 🟡 **Sealed-secrets private key single copy** (`~/.sealed-secrets/`, no backup found). Must be backed up NOW (e.g., password manager / university vault).
5. 🟡 **No off-site backup** — CNPG → MinIO and Velero → MinIO are both **in-cluster**; a full cluster loss loses backups too. Options: Cloudflare R2 (user's account; migrate to university account later) or university S3.
6. 🟡 **Worker-02 83% requests / 354% limits** post-zombie-removal — right-sizing still needed (#74).
7. 🟡 **Restore drill never performed** (neither CNPG nor Velero) — unproven recovery.
8. 🟡 **Velero inventory unverified** — schedule runs (2026-08-30), but no restore test; Backup CRs list empty via API (verify with `velero get backups`).
9. 🟢 Email: wired, placeholder SMTP (#69/#72) — awaiting university relay creds (operator step documented).
10. 🟢 DBRepo: **not in scope** (user curiosity only; decision: do not deploy).

## Operator actions — status 2026-09-05 (lead-verified)

| # | Action | Status 2026-09-05 |
|---|---|---|
| 1 | Back up sealed-secrets private key (`~/.sealed-secrets/`, single copy) | **STILL PENDING** — key pair exists (private + public pem), no backup found. Highest-priority operator step: copy to password manager / university vault |
| 2 | University IT ticket: unique `/etc/machine-id` per node + reboot | **STILL PENDING — flap risk remains**. IT's worker-02 kill+restart reconnected the agent session (streaming works), but all 3 nodes still share `de88ca16...`, so the session can drop again |
| 3 | Off-site backup decision (Cloudflare R2 vs university S3) | **STILL PENDING** — CNPG → MinIO and Velero → MinIO both in-cluster; 3 Released PVs (postgres-1, postgres-2, old redis-data) also await Task 16 cleanup |
| 4 | University SMTP relay credentials (re-seal + replace `smtp.example.org`) | **STILL PENDING** — placeholder still live 2026-09-05; image `0f685be` pinned in git and live |
| 5 | Restore drills (CNPG + Velero canary) | **STILL PENDING** — blocked until at least one backup completes (scheduler deadlock → backup-diag worker) |
| 6 | Worker-02 rebalance (issue #74) | **NEW baseline 2026-09-05**: worker-02 83% requests / 354% limits, worker-01 67% / 187% — see `docs/plans/active/2026-09-05-worker-02-rebalance.md` |

Resolved since 2026-09-03: worker-02 kubelet streaming (IT restart), postgres
reconciled to spec 1 / status 1 / ready 1 with Ready + ContinuousArchiving True,
17 of 17 ArgoCD apps Synced+Healthy, invenio web ×2 / worker ×2 / scheduler ×1
Running.

## Task Groups

- [x] **Group 1**: Document architecture + findings (this doc) ✅
- [ ] **Group 2**: Operator actions (key backup; machineID IT ticket — still pending 2026-09-05, flap risk remains; R2/university storage decision; SMTP creds — still placeholder 2026-09-05; see status table above)
- [ ] **Group 3**: #75 resolution path — **streaming restored 2026-09-05 via IT worker-02 restart**; machineID fix via IT still pending, affinity workaround no longer urgent
- [ ] **Group 4**: #74 worker-02 right-sizing — **baseline re-measured 2026-09-05** (83%/354%), plan at `2026-09-05-worker-02-rebalance.md`
- [ ] **Group 5**: Restore drills (CNPG manual backup verify → restore test in canary; Velero restore drill per README)
- [ ] **Group 6**: Off-site backup (R2 bucket + velero/CNPG re-point; migration path to university storage)

## Acceptance Criteria

- [ ] Architecture + failure-mode matrix documented (this doc)
- [ ] All operator actions listed and tracked (this doc + index)
- [ ] CNPG backup completes (blocker: #75 or affinity workaround)
- [ ] Restore drill executed at least once for CNPG and Velero
- [ ] Off-site backup target decided and configured OR explicitly deferred with owner

## Open questions / decisions needed

1. **machineID fix**: user to contact university IT for VM console access (`/etc/machine-id` regenerate + reboot on all 3 nodes). Until then, approve affinity-based CNPG move to worker-01 as workaround?
2. **Storage**: Cloudflare R2 (user account now, migrate to university later) vs university S3 — which first?
3. **Sealed-secrets key**: where to store backup (password manager / vault / university IT)?
4. **Restore drills**: OK to run CNPG restore test into a canary database (non-destructive), and Velero canary drill per README?
