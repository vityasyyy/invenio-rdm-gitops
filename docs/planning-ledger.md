# Documentation Planning Ledger

> **Purpose:** Persistent source of truth bridging the implementation plans and the actual
> codebase + live cluster state. The active plan(s) in `.opencode/plans/` and `.superpowers/sdd/`
> describe intended work; this ledger records what has actually changed, when, and why, so the
> planning never goes stale and always matches the repo + `btd-rke2` cluster.
>
> **Maintenance rule (mandatory for every coding agent):** whenever any codebase or cluster
> state changes, the agent MUST update this ledger to stay in sync with the codebase, and MUST
> document every finding (decisions, discoveries, outcomes, blockers, root causes) here before
> finishing. Do not let the planning documents drift from reality.

---

## Active Plans

| Plan file | Goal | Phase | Status (vs reality) |
|---|---|---|---|
| `.opencode/plans/2026-08-14-cluster-recovery-and-ugm-migration.md` | (1) recover `btd-rke2` to `main` baseline, (2) migrate Invenio to UGM image | Phase 1 + Phase 2 | **Phase 1 COMPLETE.** Phase 2 in progress: UGM layer vendored + image built (uid 1654, invenio-app-rdm 13.0.9), manifests + CI updated on `feat/ugm-adapt-dockerfile`. |
| `.superpowers/sdd/2026-08-14-cluster-recovery-and-ugm-migration/` | task-by-task execution ledger for the above | all | Task 1-5 complete; Task 6-10 done in branch, Task 11 not yet deployed, Task 12 pending. |

## NOTE: DBRepo-UGM is a SEPARATE product (do not conflate)

The user pointed at `gunturbudi/dbrepo-ugm` (github.com/gunturbudi/dbrepo-ugm) in the same
breath as the Invenio UGM migration. **It is NOT Invenio.** DBRepo is TU Wien's Database
Repository (v1.13.4), a full docker-compose stack: MariaDB/Postgres, Keycloak, RabbitMQ,
OpenSearch, SeaweedFS, plus metadata/consumer/search/data/dashboard services and a forked
`dbrepo-ui` (Nuxt/Vuetify). It ships as compose + nginx gateway, NOT Kubernetes-native.

This repo is InvenioRDM on Kubernetes. The Invenio-UGM migration (this plan) uses the
`invenio-ugm` instance layer only. Deploying DBRepo-UGM here would be a NEW, separate ArgoCD
app on an entirely different stack. **Decision pending from user** — do NOT start DBRepo work
without explicit go-ahead. Captured here so the planning stays synced.

---

## Verified Cluster + Codebase State (2026-08-19)

### Nodes — ALL READY (worker-01 RECOVERED)

| Node | Status | Note |
|---|---|---|
| `ubuntu-btd-kubernetes-server` | Ready | control-plane (taint `CriticalAddonsOnly`) |
| `ubuntu-btd-kubernetes-worker-01` | Ready | **recovered since last assessment (was NotReady/evicted); up ~28h** |
| `ubuntu-btd-kubernetes-worker-02` | Ready | 8Gi |

> **Change vs plan/assessment:** The 2026-08-14/15 assessment recorded worker-01 evicted and
> only worker-02 schedulable. It is now **Ready again**, which is why the cluster has fully
> converged (see below). The plan's "single-node right-sizing" assumption is obsolete — capacity
> is no longer the hard constraint.

### ArgoCD apps — ALL 16 Synced + Healthy

`apps`, `argocd-image-updater`, `argocd-self`, `cloudflared`, `invenio-bootstrap`,
`invenio-opensearch`, `invenio-postgresql`, `invenio-redis`, `loki`, `minio`, `minio-extras`,
`monitoring`, `monitoring-extras`, `sealed-secrets`, `security-policies`, `traefik`, `velero`
— every app `Synced` + `Healthy`. `invenio-bootstrap` (previously Degraded) is now Healthy.

### Invenio workloads

| Workload | Replicas | Status | Node |
|---|---|---|---|
| `invenio-web` deploy | 2/2 | Running (8 restarts 4d10h ago, now stable) | worker-02 |
| `invenio-worker` deploy | 2/2 | Running | worker-01 |
| `invenio-setup` job | 1/1 | Complete (33d ago) | — |

### HPA (live == git == right-sized)

- `invenio-web-hpa`: min 1 / max 2, cpu 0%, mem 88%
- `invenio-worker-hpa`: min 1 / max 2, cpu 85%, mem 76%

The out-of-band HPA patch from the deadlock-break session is now reconciled with git; live
values match `k8s/apps/invenio/invenio-hpa.yaml`.

### Endpoint status (live, 2026-08-19)

| Endpoint | Code | Verdict |
|---|---|---|
| `https://invenio.vityasy.me/ping` | 200 | UI healthy |
| `https://invenio.vityasy.me/` | 200 | UI serves (InvenioRDM branding, NOT yet UGM) |
| `https://invenio.vityasy.me/api` | 404 | no route at `/api` root (expected); real endpoints work |
| `https://api-invenio.vityasy.me/api` | 404 | same (root has no route) |
| `https://api-invenio.vityasy.me/api/records?size=1` | **200** | **FIXED** — returns proper empty search response |
| `POST https://invenio.vityasy.me/api/records?expand=1` | **403** | **FIXED** — was the historical 500; now returns 403 (unauthenticated), proving the 500 saga is gone |
| `https://api-invenio.vityasy.me/api/communities` | 200 | functional |
| `https://api-invenio.vityasy.me/login/` | 200 | HTML form login (session cookie) — this is the real auth path, NOT `/api/accounts/login` |
| `https://argocd.vityasy.me` | 200 | healthy |

### Image / storage / config (baseline, pre-UGM)

- Live `invenio-web` image: `ghcr.io/vityasyyy/invenio-rdm-custom@sha256:e626ca7f...` (pinned)
- Web args: `gunicorn invenio_app.wsgi:application --bind 0.0.0.0:5000 --workers 2 --threads 2`
- Worker args: `celery -A invenio_app.celery worker --concurrency 2 ...`
- Service `invenio-web` port 8000 → container port `http`(5000); IngressRoutes `invenio-ui`
  (Host `invenio.vityasy.me`) + `invenio-api` (Host `api-invenio.vityasy.me`) both → `invenio-web:8000`
- Kustomization still lists debug artifacts (`debug-rbac.yaml`, `invenio-debug-output-cm.yaml`,
  `invenio-debug-job.yaml`) — **Phase 1 Task 5 (remove debug artifacts) NOT yet done.**
- `DEBUG_PROGRESS.md` still present at repo root — Phase 2 Task 12 deletion pending.
- No `docker/ugm/`, no UGM vendoring, no `invenio-scheduler` deployment, no `invenio-data-pvc`
  — **Phase 2 entirely NOT started.**

---

## Known Blocker(s)

0. **RESOLVED (2026-08-19) — GHCR pull secret token was dead; UGM deploy unblocked.**
   - User supplied a valid PAT (`write:packages` scope); verified it pulls `invenio-ugm`.
   - Re-sealed `ghcr-pull-secret` via kubeseal (PR #61, merged `89e7412`); updated
     `secrets/ghcr/pat.txt` (gitignored).
   - After the fix: UGM pods pulled the image, all came up Running, setup job completed
     [9/9], app Healthy, UI shows UGM branding.
   - **API regression found + fixed:** the UGM `uwsgi_ui.ini` used `invenio_app.wsgi_ui`
     (UI-only) → `/api/records` 404. Fixed to `invenio_app.wsgi` (combined UI+REST), PR #62
     merged `926448c`. Rebuilt image `609eacc...`.
   - Image-updater reported `IMAGES: 0` and didn't write a digest, so `:latest` kept serving
     the old image → pinned the digest in kustomization (PR #63, merged `3c5af3d`).
   - **Verified via public endpoints (VPN flaky):** UI `/` 200, `/ping` 200, API
     `/api/records` 200, `/api/communities` 200, UGM branding live. Task 11 deploy SUCCESSFUL.

1. **RESOLVED (2026-08-19) — OpenSearch `red`, 141 unassigned shards → API 500.** Fixed via
   clean-slate: `DELETE /_all` indices + `invenio index init` + remove stale `.opensearch-sap-log-types-config`
   primary. OpenSearch now `yellow` (all primaries active; 32 unassigned are only replicas,
   expected on a single data node). `/api/records` returns 200 with empty search results —
   **the 500 saga gate is cleared.** Root cause was stale NFS `write.lock` files left by the
   2026-08-14 force-delete (see Findings log).
2. **Plan's smoke-test auth path is WRONG — not a real blocker.** The plan uses
   `/api/accounts/login` for a token; this image serves HTML form login at `/login/`
   (200, session cookie). There is no JSON token login endpoint exposed. The correct
   authenticated API flow needs a session cookie (CSRF + session) rather than a Bearer token.
   The historical 500 is confirmed gone regardless: unauthenticated `POST /api/records?expand=1`
   now returns `403` (not 500), and GET `/api/records` returns 200.
3. **Pre-existing infra issue:** apiserver→worker-02 kubelet streaming 502 (port 9345→10250)
   still blocks `kubectl logs`/`exec`/port-forward to worker-02 pods (web, opensearch) and CNPG
   WAL-archive/backups. Out of scope per prior constraints; do NOT fix. Worker pods on
   worker-01 remain readable — used to probe OpenSearch and run invenio CLI via the cluster
   service.

---

## Phase-by-Phase Status vs the Active Plan

### PHASE 1 — Recover Cluster to Baseline (status: ~converged, gates open)

| Task | Status | Evidence |
|---|---|---|
| Task 1: Connect VPN + verify connectivity | ✅ Done | kubectl works, all nodes Ready |
| Task 2: Assess drift | ✅ Done | `docs/cluster-assessment-2026-08-14.md` |
| Task 3: Reconcile to main | ✅ Done | all 16 apps Synced+Healthy |
| Task 4: Verify baseline (500 saga) | ✅ **Gate CLEARED (root cause found + fixed)** | OpenSearch `red` from stale `write.lock` → clean-slate re-init → `/api/records` 200. Login 404/users 500 remain separately (see blockers). |
| Task 5: Remove debug artifacts + rotate ArgoCD pass | ✅ **Done** | PR #58 merged (`4d2a7b2`); debug CM/role/job pruned from cluster (NotFound); ArgoCD pass rotated + stored in `secrets/argocd-admin-password.txt` (gitignored) |

### PHASE 2 — Migrate to UGM Image (status: Tasks 6-11 DONE, Task 12 pending)

| Task | Status | Evidence |
|---|---|---|
| Task 6: Vendor UGM layer | ✅ Done | PR #60; `docker/ugm/` + site/templates/static/app_data/translations/assets vendored |
| Task 7: Adapt Dockerfile/config | ✅ Done | non-root uid 1654, HTTP uwsgi, exec-form CMD, env-driven invenio.cfg |
| Task 8: Update deployments (image/args/storage/scheduler) | ✅ Done | web/worker→UGM image + NFS PVCs, new scheduler, data/archive PVCs |
| Task 9: Rewrite ConfigMap + setup job | ✅ Done | JSON search hosts, Redis broker, local files location, idempotent [9/9] setup |
| Task 10: CI build workflow + image-updater | ✅ Done | invenio-ugm image build + verify, image-updater CR updated |
| Task 11: Clean-slate deploy + E2E verification | ✅ **Done** | UGM deployed, app Healthy, setup [9/9], UI+API 200, UGM branding live. API 404 regression found+fixed (combined wsgi). |
| Task 12: Cleanup + documentation | ⛔ Not started | `DEBUG_PROGRESS.md` still present |

---

## Git / Branch State (2026-08-19)

- `main` (62d4f95) = local, diverged from `origin/main` (ahead 1, behind 2)
- `origin/main` = `3d610e9` (PR #55 merged; PR #56 doc-only still OPEN on
  `feat/53-recovery-verification`)
- `feat/53-cluster-recovery` = right-sizing for single-node (1558167) — **now superseded by
  worker-01 recovery; the right-sizing values are correct and live anyway**
- `feat/ugm-migration` worktree exists at c675d7a but contains no UGM work yet
- Untracked: `.opencode/plans/2026-08-14-cluster-recovery-and-ugm-migration.md` (the plan)
- Modified: `.gitignore`

---

## Findings Log (append-only)

### 2026-08-19 — session open
- worker-01 is Ready again (~28h); all 16 ArgoCD apps Synced+Healthy; invenio-web/worker 2/2.
- The plan's single-node assumption is obsolete — capacity is no longer the blocker.
- **Root-caused the API gate:** OpenSearch `red` (141 unassigned shards) from stale NFS
  `write.lock` left by the 2026-08-14 force-delete. Worker search scans threw 503 → `/api/records` 500.
- **FIXED the API gate:** clean-slate `DELETE /_all` + `invenio index init` + removed stale
  `.opensearch-sap-log-types-config`. OpenSearch now `yellow`; GET `/api/records` returns 200 and
  unauthenticated `POST /api/records?expand=1` returns 403 (was 500) — **500 saga confirmed gone.**
- AUTH PATH CLARIFIED: plan's `/api/accounts/login` token flow doesn't exist here; real auth is
  HTML form login at `/login/` (session cookie). Not a blocker.
- **Phase 1 Task 5 DONE:** PR #58 merged (`4d2a7b2`) removed debug artifacts (CM/role/job pruned,
  verified NotFound); ArgoCD admin password rotated and stored in gitignored
  `secrets/argocd-admin-password.txt`. Phase 1 COMPLETE.
- Phase 2 (UGM migration) started (Task 6).
- `kubectl logs`/`exec`/port-forward to worker-02 web/opensearch pods blocked by the known
  kubelet streaming 502 (probed OpenSearch and ran invenio CLI from a worker-01 pod instead).
- Ledger committed + PR #57 opened (`docs/planning-ledger` branch).
