# Cluster Recovery to Baseline + UGM Image Migration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** (1) Recover the `btd-rke2` cluster to the git `main` baseline (all ArgoCD apps Synced+Healthy, Invenio reachable and functional at `invenio.vityasy.me`), then (2) migrate the Invenio image to a UGM-branded instance built from `gunturbudi/invenio-ugm`, with image pushes coming from this repo.

**Architecture:** Phase 1 reconciles the live cluster to `main` (c675d7a) via ArgoCD and verifies the Invenio baseline end-to-end (including the `POST /api/records?expand=1` 500 saga). Phase 2 vendors the UGM instance layer (Dockerfile, invenio.cfg, uwsgi configs, site/templates/static/app_data/translations) into this repo, builds it as `ghcr.io/vityasyyy/invenio-ugm` via the existing GHCR CI workflow, switches file storage from MinIO/S3 to NFS-backed PVCs (fresh start — no data preservation), and adapts the K8s manifests (uwsgi instead of gunicorn, local file location, `rdm-` search index prefix, new scheduler deployment).

**Tech Stack:** Kubernetes (RKE2 v1.32.6, Rancher), ArgoCD v2.12, Kustomize, Helm, CloudNativePG, Sealed Secrets, Traefik, OpenSearch 2, Redis, MinIO (to be retired for app files), NFS CSI (`btd-nfs` StorageClass), GitHub Actions (GHCR + CI), InvenioRDM v13.

## Global Constraints

- **Domain stays**: `invenio.vityasy.me` (UI) and `api-invenio.vityasy.me` (API) — do not change ingress hosts or Cloudflare tunnel config.
- **Image registry**: builds and pushes happen from **this repo** (`vityasyyy/invenio-rdm-gitops`) to `ghcr.io/vityasyyy/invenio-ugm`. Do not push from the upstream `gunturbudi` repo.
- **Fresh start**: existing records/files/data do **not** need to be preserved (user-confirmed). Wiping the invenio DB schema + OpenSearch indices is acceptable and recommended for a deterministic migration.
- **Cluster identity**: `btd-rke2`, Rancher-managed RKE2 at `https://10.17.104.130/k8s/clusters/c-m-vrpr6n7h`, reachable only via university VPN. Storage class for PVCs: `btd-nfs` (nfs.csi.k8s.io).
- **GitOps invariants**: every change goes through the repo's issue-to-PR workflow (issue → branch `<prefix>/<issue>-<slug>` → PR → squash merge → ArgoCD auto-sync, selfHeal+prune). Branch protection: 1 required review, linear history, CI must pass (yamllint, kustomize render, kubeconform, selector validation, gitleaks).
- **Security invariants**: all pods `runAsNonRoot`, uid/gid **1654** (image must ship a user with this uid), capabilities dropped, `seccompProfile: RuntimeDefault`, namespace quota `invenio-quota` (pods 30, PVCs 20) must not be exceeded.
- **Version floor**: InvenioRDM `invenio-app-rdm ~=13.0.0` with `opensearch2` extras (UGM Pipfile pins this); Python 3.9 runtime from `registry.cern.ch/inveniosoftware/almalinux:1`.
- **Debug artifacts** (`invenio-debug-job.yaml`, `debug-rbac.yaml`, `invenio-debug-output-cm.yaml`) are temporary and must be removed during Phase 1; `argocd-log-reader.yaml` and the project whitelist entries stay.

---

# PHASE 1 — Recover Cluster to Baseline

## Task 1: Connect VPN and Verify Cluster Connectivity

**Files:** none (operator action + read-only commands)

**Interfaces:**
- Consumes: user-supplied VPN connection to the university network
- Produces: confirmed `kubectl` access to `btd-rke2`; baseline snapshot of cluster state used by Task 2

- [ ] **Step 1: User connects the university OpenVPN**

The user must connect OpenVPN (agent already running on the machine) before any of the following steps can succeed.

- [ ] **Step 2: Verify API server reachability**

Run:
```bash
kubectl cluster-info --request-timeout=10s
kubectl get nodes --request-timeout=10s
```
Expected: cluster info prints and all 3 nodes (`control-plane` 10.17.117.41, `worker-01` .42, `worker-02` .43) return `Ready`.
If `context deadline exceeded` persists: the VPN is not connected or the API server IP changed — stop and check with the user before continuing.

- [ ] **Step 3: Verify ArgoCD is reachable**

Run:
```bash
kubectl -n argocd get pods --request-timeout=10s | head -20
kubectl -n argocd get applications --request-timeout=10s
```
Expected: argocd-server/application-controller/repo-server pods `Running`; the full app list (16 apps: security-policies, traefik, sealed-secrets, argocd-self, cloudflared, minio, minio-extras, velero, argocd-image-updater, loki, monitoring, monitoring-extras, invenio-opensearch, invenio-redis, invenio-postgresql, invenio-bootstrap) is returned.

- [ ] **Step 4: Commit the assessment as evidence**

Create a T2 issue `[T2] Cluster recovery to baseline` (or reuse if one exists), branch `feat/<n>-cluster-recovery`, and commit a `docs/cluster-assessment-2026-08-14.md` file containing the `kubectl get applications -o wide` and `kubectl get nodes` output, then continue to Task 2 on the same branch.

---

## Task 2: Assess What Has Drifted (Root Cause)

**Files:**
- Create: `docs/cluster-assessment-2026-08-14.md` (evidence, appended to)

**Interfaces:**
- Consumes: Task 1 connectivity
- Produces: a definitive list of OutOfSync/Degraded apps and missing resources; input for Task 3

- [ ] **Step 1: Snapshot ArgoCD app health**

Run:
```bash
kubectl -n argocd get applications -o custom-columns='NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status,REVISION:.status.sync.revision,OPERATION:.status.operationState.phase'
```
Record every row where SYNC ≠ `Synced` or HEALTH ≠ `Healthy`.

- [ ] **Step 2: Verify the invenio IngressRoute gap (live 404 diagnosis)**

Run:
```bash
kubectl get ingressroute -A
kubectl -n invenio get all
kubectl -n invenio get deployment invenio-web invenio-worker -o wide
```
Expected finding (matches the live evidence gathered 2026-08-14): `invenio-ui`/`invenio-api` IngressRoutes missing or the whole `invenio-bootstrap` app Degraded/OutOfSync — this is why `invenio.vityasy.me` returns Traefik `404 page not found` while `argocd.vityasy.me` returns 200.

- [ ] **Step 3: Check the invenio-bootstrap app condition**

Run:
```bash
kubectl -n argocd get application invenio-bootstrap -o yaml | grep -A 30 'status:'
```
Look for `conditions` (SyncError messages), `health` reason, and whether resources were pruned.

- [ ] **Step 4: Check all other namespaces for drift**

Run:
```bash
kubectl get pods -A --field-selector=status.phase!=Running --request-timeout=10s
kubectl -n database get cluster,backup 2>/dev/null | head
kubectl -n minio get pvc,jobs | head
kubectl -n search get statefulset,pods | head
kubectl -n redis get pods | head
```
Record anything not Running/Healthy (CNPG cluster phase, MinIO bucket job, OpenSearch statefulset).

- [ ] **Step 5: Record findings in the assessment doc and commit**

Append the findings with the exact commands and outputs to `docs/cluster-assessment-2026-08-14.md`, then commit:
```bash
git add docs/cluster-assessment-2026-08-14.md
git commit -m "docs: record cluster drift assessment (#<issue>)"
```

---

## Task 3: Reconcile Cluster to Git Main

**Files:** none (cluster operations; ArgoCD reads `main` as-is)

**Interfaces:**
- Consumes: Task 2 drift list
- Produces: all 16 ArgoCD apps `Synced` + `Healthy`; Invenio routed again at `invenio.vityasy.me`/`api-invenio.vityasy.me`

- [ ] **Step 1: Re-sync the invenio-bootstrap app**

If ArgoCD CLI is available:
```bash
argocd app sync invenio-bootstrap --prune
argocd app wait invenio-bootstrap --health --timeout 300
```
Otherwise, trigger via kubectl (selfHeal is enabled, so annotating a sync is enough — a no-op resource update forces a sync):
```bash
kubectl -n argocd patch application invenio-bootstrap --type merge \
  -p '{"metadata":{"annotations":{"argocd.argoproj.io/sync-options":"TriggerRefresh=true"}}}'
kubectl -n argocd rollout restart deployment/argocd-application-controller  # force refresh if nothing happens
```
Then wait and re-check:
```bash
kubectl -n argocd get application invenio-bootstrap -o custom-columns='SYNC:.status.sync.status,HEALTH:.status.health.status'
```
Expected: `Synced` + `Healthy`.

- [ ] **Step 2: Sync remaining unhealthy apps one at a time**

For each app flagged in Task 2 Step 1 that is not `Synced`/`Healthy`, run the same sync command (replace app name), and for any `SyncError` read the `operationState` message:
```bash
kubectl -n argocd get application <app> -o jsonpath='{.status.operationState.message}'
```
If a specific app is stuck (e.g., CNPG operator upgrade conflict), debug it individually — do **not** batch-fix apps that aren't broken. Re-run the full app list until everything is `Synced`/`Healthy`.

- [ ] **Step 3: Verify Invenio routing is restored**

Run:
```bash
curl -s -m 15 -o /dev/null -w "%{http_code}\n" https://invenio.vityasy.me/ping
curl -s -m 15 -o /dev/null -w "%{http_code}\n" https://api-invenio.vityasy.me/api
curl -s -m 15 -o /dev/null -w "%{http_code}\n" https://argocd.vityasy.me
```
Expected: `/ping` returns `200` (the Traefik `404 page not found` must be gone), `/api` returns `200` (or `3xx`), argocd stays `200`.

- [ ] **Step 4: Verify pods healthy**

```bash
kubectl -n invenio get pods -o wide
kubectl -n invenio rollout status deploy/invenio-web --timeout=300s
kubectl -n invenio rollout status deploy/invenio-worker --timeout=300s
```
Expected: `invenio-web` (2 replicas) and `invenio-worker` (2 replicas) `Running`/`Ready`, probes passing.

- [ ] **Step 5: Commit the recovery outcome to the assessment doc**

Append final app table + endpoint status to `docs/cluster-assessment-2026-08-14.md` and commit.

---

## Task 4: Verify Invenio Baseline Functionality (the 500 Saga)

**Files:** none (cluster verification only)

**Interfaces:**
- Consumes: Task 3 (Invenio up)
- Produces: verdict on whether `POST /api/records?expand=1` still 500s; decision input: if it still 500s, re-open the DEBUG_PROGRESS hypotheses before proceeding to Phase 2

- [ ] **Step 1: Create a smoke-test user and capture an auth token**

Exec into a web pod (image `ghcr.io/vityasyyy/invenio-rdm-custom:latest` at digest pinned in `kustomization.yaml`):
```bash
POD=$(kubectl -n invenio get pod -l app.kubernetes.io/name=invenio-web -o jsonpath='{.items[0].metadata.name}')
kubectl -n invenio exec -it "$POD" -- invenio users create smoke-test@example.org --password 'SmokeTest!234' --active
kubectl -n invenio exec -it "$POD" -- invenio roles add smoke-test@example.org admin
```
Then:
```bash
curl -s -m 20 -X POST https://invenio.vityasy.me/api/accounts/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"smoke-test@example.org","password":"SmokeTest!234"}'
```
Capture the `access_token` from the JSON response into `$TOKEN`.

- [ ] **Step 2: Reproduce the historical 500**

```bash
curl -s -m 30 -o /tmp/records-response.json -w "HTTP %{http_code}\n" \
  -X POST 'https://invenio.vityasy.me/api/records?expand=1' \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"access_right":"public","files":{"enabled":true},"metadata":{"title":"smoke test","upload_type":{"type":"publication"},"publication_date":"2026-08-14"},"rights":[{"id":"cc-by-4.0"}]}'
cat /tmp/records-response.json
```
Record the status code. Note the record PID (e.g. `abcd-ef12-3456-7890`) for Step 3.
If `500`: the baseline is still broken — **stop**, re-run the debug loop from `DEBUG_PROGRESS.md` (read the debug ConfigMap output, evaluate hypotheses A–D) before proceeding. Do not skip this gate.

- [ ] **Step 3: Upload a file through MinIO round-trip**

```bash
echo "hello baseline $(date +%s)" > /tmp/smoke.txt
PID=<record-pid-from-step-2>
curl -s -m 30 -X POST "https://invenio.vityasy.me/api/records/$PID/draft/files" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"smoke.txt"}' | head -c 500
curl -s -m 120 -X PUT "https://invenio.vityasy.me/api/records/$PID/draft/files/smoke.txt/content" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/octet-stream' \
  --data-binary @/tmp/smoke.txt
curl -s -m 30 -X POST "https://invenio.vityasy.me/api/records/$PID/draft/actions/publish" \
  -H "Authorization: Bearer $TOKEN" | head -c 300
```
Expected: file uploaded (no 500), publish succeeds. This verifies the S3/MinIO path (location `s3://invenio-rdm/files/`) end-to-end — the exact path that motivated the whole debug saga.

- [ ] **Step 4: Clean up the smoke-test user (optional, do at end of Phase 1)**

```bash
kubectl -n invenio exec -it "$POD" -- invenio users delete smoke-test@example.org 2>/dev/null || true
```

- [ ] **Step 5: Record verdict in the assessment doc + commit**

`docs/cluster-assessment-2026-08-14.md`: add the endpoint status codes, the record PID, and the verdict ("baseline functional" or "500 persists → gate"). Commit.

---

## Task 5: Remove Debug Artifacts and Rotate ArgoCD Admin Password

**Files:**
- Delete: `k8s/apps/invenio/invenio-debug-job.yaml`, `k8s/apps/invenio/debug-rbac.yaml`, `k8s/apps/invenio/invenio-debug-output-cm.yaml`
- Modify: `k8s/apps/invenio/kustomization.yaml` (remove the three deleted files from `resources:`)
- Keep: `k8s/apps/invenio/argocd-log-reader.yaml`, the `Role`/`RoleBinding` whitelist entries in `k8s/infra/argocd/projects/invenio-project.yaml`
- Delete (at end of Phase 2, Task 12): `DEBUG_PROGRESS.md`

**Interfaces:**
- Consumes: Task 4 gate passed (baseline verified) — do **not** remove debug tooling before the 500 gate passes
- Produces: clean invenio app layer; rotated ArgoCD admin credentials

- [ ] **Step 1: Remove debug resources from the kustomization**

Edit `k8s/apps/invenio/kustomization.yaml` — `resources:` list loses the three lines:
```yaml
  - debug-rbac.yaml
  - invenio-debug-output-cm.yaml
  - invenio-debug-job.yaml
```

- [ ] **Step 2: Delete the three files**

```bash
git rm k8s/apps/invenio/invenio-debug-job.yaml \
       k8s/apps/invenio/debug-rbac.yaml \
       k8s/apps/invenio/invenio-debug-output-cm.yaml
```

- [ ] **Step 3: Validate renders locally**

```bash
./scripts/ci-render-manifests.sh
./scripts/ci-validate-selectors.sh
```
Expected: both pass (kustomize build succeeds; no dangling references; no secrets flagged).

- [ ] **Step 4: Open PR and merge (issue-to-PR workflow)**

```bash
git checkout -b fix/<issue>-remove-debug-artifacts
git add k8s/apps/invenio/kustomization.yaml
git commit -m "fix: remove temporary debug job and RBAC artifacts (#<issue>)"
gh pr create --title "[T1] Remove temporary debug artifacts" --squash --body "Closes #<issue>"
# after review:
gh pr merge <number> --squash --delete-branch
```
Wait for ArgoCD to sync (selfHeal) and confirm the three resources are gone:
```bash
kubectl -n invenio get configmap invenio-debug-output,role debug-configmap-writer,rolebinding debug-configmap-writer 2>&1
```
Expected: `NotFound` for all three.

- [ ] **Step 5: Rotate the ArgoCD admin password**

The previously exposed password must not remain valid:
```bash
NEW_PASS='<generate: openssl rand -base64 24>'
HASH=$(htpasswd -nbBC 10 "" "$NEW_PASS" | tr -d ':\n')
kubectl -n argocd patch secret argocd-secret --type merge -p \
  "{\"stringData\":{\"admin.password\":\"$HASH\",\"admin.passwordMtime\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}}"
kubectl -n argocd rollout restart deploy/argocd-server
```
Verify: `curl -s -o /dev/null -w '%{http_code}' https://argocd.vityasy.me` still `200`, and log in with the new password in the UI.
Record the new password in `secrets/` (gitignored) as `secrets/argocd-admin-password.txt` — never commit it.

- [ ] **Step 6: Update DEBUG_PROGRESS.md status**

Mark the Phase 1 outcome (baseline restored, 500 gate result) at the top of `DEBUG_PROGRESS.md`; full removal happens in Task 12 after the UGM migration is verified.

---

# PHASE 2 — Migrate to UGM-Personalized Image

## Task 6: Create the T3 Issue and Vendor the UGM Instance Layer

**Files:**
- Create: `docker/ugm/Dockerfile`, `docker/ugm/invenio.cfg`, `docker/ugm/uwsgi_ui.ini` (patched), `docker/ugm/uwsgi_rest.ini` (patched), `docker/ugm/entrypoint.sh` (optional, if needed)
- Replace (contents from `gunturbudi/invenio-ugm@main`): `site/`, `templates/`, `static/`, `app_data/`, `translations/` (add `assets/` if not present — UGM branding uses `assets/less/site/globals/`)
- Create: `Pipfile` + `Pipfile.lock` (from UGM repo, pinned `invenio-app-rdm ~=13.0.0`, extras `opensearch2`)

**Interfaces:**
- Consumes: upstream `gunturbudi/invenio-ugm@main` (fetched at planning time; re-verify the commit SHA at execution time)
- Produces: `docker/ugm/` and vendored instance layer that Task 7 builds; the image name contract `ghcr.io/vityasyyy/invenio-ugm` used by Tasks 8–10

- [ ] **Step 1: Create the T3 issue with risk/rollback sections**

`[T3] Migrate Invenio image to UGM-personalized instance` — full template + Risk Assessment + Rollback Plan + Affected Services (invenio-web, invenio-worker, invenio-setup, ingress, storage).

- [ ] **Step 2: Fetch the upstream UGM files**

```bash
UPSTREAM=https://raw.githubusercontent.com/gunturbudi/invenio-ugm/main
mkdir -p docker/ugm
for f in Dockerfile Pipfile Pipfile.lock invenio.cfg .dockerignore; do
  curl -fsSL "$UPSTREAM/$f" -o "docker/ugm/$(basename $f)"
done
curl -fsSL "$UPSTREAM/docker/uwsgi/uwsgi_ui.ini" -o docker/ugm/uwsgi_ui.ini
curl -fsSL "$UPSTREAM/docker/uwsgi/uwsgi_rest.ini" -o docker/ugm/uwsgi_rest.ini
```
Record the fetched commit SHA (use the GitHub API `commits/main` sha) in the issue.

- [ ] **Step 3: Vendor the instance layer directories**

Clone the upstream repo to a temp dir and copy the customization layer into this repo (replacing the old `vityasyyy` branding):
```bash
git clone --depth 1 https://github.com/gunturbudi/invenio-ugm.git /tmp/invenio-ugm
rm -rf site templates static app_data translations assets
cp -r /tmp/invenio-ugm/site /tmp/invenio-ugm/templates /tmp/invenio-ugm/static /tmp/invenio-ugm/app_data /tmp/invenio-ugm/translations .
cp -r /tmp/invenio-ugm/assets . 2>/dev/null || true
cp /tmp/invenio-ugm/.dockerignore .
```
Check for leftover references to the old layout (`grep -rn "vityasyyy\|invenio-rdm-custom" site templates static app_data translations assets docker/ugm` — should return nothing except the image name in `.github/workflows/build-image.yaml`, which Task 10 fixes).

- [ ] **Step 4: Move the Pipfile into the Dockerfile context**

The UGM Dockerfile expects `Pipfile`/`Pipfile.lock` in the build context root. Move them to `docker/ugm/` and adjust the Dockerfile copy step (done in Task 7) so the build context stays the repo root.

- [ ] **Step 5: Commit the vendored layer on the migration branch**

```bash
git checkout -b feat/<issue>-ugm-image-migration
git add -A
git commit -m "feat: vendor UGM Invenio instance layer (#<issue>)"
```
Do **not** merge yet — Tasks 7–10 must land in reviewable order. (Recommendation: this task and Tasks 7–10 form a single T3 PR reviewed as a whole; commit granularly within it.)

---

## Task 7: Adapt the Dockerfile + Runtime Config for Kubernetes

**Files:**
- Modify: `docker/ugm/Dockerfile` (non-root user, Pipfile path, no root entrypoint)
- Modify: `docker/ugm/uwsgi_ui.ini`, `docker/ugm/uwsgi_rest.ini` (HTTP socket instead of uwsgi protocol for K8s probes/Traefik)
- Modify: `docker/ugm/invenio.cfg` (deterministic env overrides: SITE URLs, TRUSTED_HOSTS incl. API host, Celery broker via Redis, SEARCH_HOSTS)

**Interfaces:**
- Consumes: Task 6 vendored files
- Produces: an image that (a) runs as uid 1654 non-root, (b) speaks HTTP on 5000, (c) reads the cluster ConfigMap keys from Task 9

- [ ] **Step 1: Patch the Dockerfile — non-root user and correct Pipfile path**

`docker/ugm/Dockerfile` changes (upstream starts from `registry.cern.ch/inveniosoftware/almalinux:1`):
```dockerfile
COPY site ./site
COPY Pipfile Pipfile.lock ./            # → changed to:
COPY docker/ugm/Pipfile docker/ugm/Pipfile.lock ./
RUN pipenv install --deploy --system

COPY ./docker/uwsgi/ ${INVENIO_INSTANCE_PATH}   # → changed to:
COPY ./docker/ugm/uwsgi_ui.ini ./docker/ugm/uwsgi_rest.ini ${INVENIO_INSTANCE_PATH}
COPY ./docker/ugm/invenio.cfg ${INVENIO_INSTANCE_PATH}
COPY ./templates/ ${INVENIO_INSTANCE_PATH}/templates/
COPY ./app_data/ ${INVENIO_INSTANCE_PATH}/app_data/
COPY ./translations/ ${INVENIO_INSTANCE_PATH}/translations/
COPY ./ .

RUN cp -r ./static/. ${INVENIO_INSTANCE_PATH}/static/ && \
    cp -r ./assets/. ${INVENIO_INSTANCE_PATH}/assets/ && \
    invenio collect --verbose  && \
    invenio webpack buildall

# K8s hardening: non-root user with the repo's pinned uid/gid 1654
RUN groupadd -r invenio --gid=1654 && \
    useradd -r -g invenio --uid=1654 --home-dir=/opt/invenio --shell=/bin/bash invenio && \
    chown -R invenio:invenio ${INVENIO_INSTANCE_PATH}

USER invenio
EXPOSE 5000
```
Keep upstream's `ENTRYPOINT [ "bash", "-c"]` — the K8s manifests pass the real command (Task 8). Delete the old `docker/invenio/` directory (`entrypoint.sh`, `theme.config`, `requirements.txt`, `Dockerfile`, `invenio.cfg`) since it is superseded.

- [ ] **Step 2: Patch the uwsgi ini files — HTTP socket**

`docker/ugm/uwsgi_ui.ini`:
```ini
[uwsgi]
http = 0.0.0.0:5000          # was: socket = 0.0.0.0:5000
stats = 0.0.0.0:9000
module = invenio_app.wsgi_ui:application
master = true
die-on-term = true
processes = 2
threads = 2
single-interpreter = true
buffer-size = 8192
wsgi-disable-file-wrapper = true
```
`docker/ugm/uwsgi_rest.ini`: same change (keep the upstream rest-module line).

- [ ] **Step 3: Patch `docker/ugm/invenio.cfg` — deterministic K8s overrides**

Append this block (env vars are read explicitly so behavior does not depend on Invenio's env-var loader precedence):
```python
# ==================== Kubernetes cluster overrides ====================
# Deterministic overrides for the GitOps deployment. The image defaults
# come from env vars, so the ConfigMap controls everything at runtime.

# Site URLs (UI host stays invenio.vityasy.me; API host is separate)
SITE_UI_URL = os.environ.get("INVENIO_SITE_UI_URL", INSTANCE_UI_URL)
SITE_API_URL = os.environ.get("INVENIO_SITE_API_URL", f"{INSTANCE_UI_URL}/api")

# Trusted hosts: UI + API hosts + k8s internals
_api_host = os.environ.get("INVENIO_SITE_API_URL", "").replace("https://", "").replace("http://", "")
TRUSTED_HOSTS = [INSTANCE_DOMAIN, _api_host] + ["0.0.0.0", "localhost", "127.0.0.1"] \
    if _api_host and _api_host != INSTANCE_DOMAIN else TRUSTED_HOSTS

# Search hosts — cluster's OpenSearch via the ExternalName service.
# UGM default is localhost:9200 which does not exist in-cluster.
import json as _json
SEARCH_HOSTS = _json.loads(os.environ.get("INVENIO_SEARCH_HOSTS", "[]")) or SEARCH_HOSTS

# Celery broker/backend — the cluster uses Redis (no RabbitMQ).
CELERY_BROKER_URL = os.environ.get("INVENIO_CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("INVENIO_CELERY_RESULT_BACKEND", "redis://localhost:6379/2")
BROKER_URL = CELERY_BROKER_URL
```

- [ ] **Step 4: Build the image locally (or via workflow_dispatch) and smoke-test it**

Trigger the CI build after Task 10 (which renames the workflow target); until then, a local build verifies the Dockerfile:
```bash
docker buildx build --platform linux/amd64 -t ghcr.io/vityasyyy/invenio-ugm:local -f docker/ugm/Dockerfile .
docker run --rm --entrypoint /bin/bash ghcr.io/vityasyyy/invenio-ugm:local -c \
  "id -u; id -g; uwsgi --version; invenio --version; python -c 'import invenio_app; print(invenio_app.__version__)'"
```
Expected: `1654`, `1654`, uwsgi version, Invenio v13 printout. Also verify the entrypoint accepts the K8s command:
```bash
docker run --rm -d --name ugm-test ghcr.io/vityasyyy/invenio-ugm:local uwsgi --ini /opt/invenio/var/instance/uwsgi_ui.ini
sleep 5; curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5000/ping 2>/dev/null || true
docker logs ugm-test 2>&1 | tail -5; docker rm -f ugm-test
```
(5000 is not published in this one-off run — the point is the container starts and uwsgi logs show the HTTP socket binding; full HTTP check happens in Task 11 in-cluster.)

- [ ] **Step 5: Commit**

```bash
git add docker/ugm/ -A && git rm -r docker/invenio/
git commit -m "feat: adapt UGM image for Kubernetes (non-root, HTTP uwsgi, env-driven config) (#<issue>)"
```

---

## Task 8: Update Deployments (image, uwsgi args, storage, scheduler)

**Files:**
- Modify: `k8s/apps/invenio/invenio-deployment.yaml` (image, args, PVC mounts)
- Modify: `k8s/apps/invenio/invenio-worker-deployment.yaml` (image, PVC mounts)
- Create: `k8s/apps/invenio/invenio-scheduler-deployment.yaml` (celery beat, singleton)
- Create: `k8s/apps/invenio/invenio-data-pvc.yaml` (RWX PVCs for data + archive)
- Modify: `k8s/apps/invenio/kustomization.yaml` (image name, new resources)

**Interfaces:**
- Consumes: Task 7 image contract (`ghcr.io/vityasyyy/invenio-ugm`, uwsgi on 5000, uid 1654, instance path `/opt/invenio/var/instance`); Task 9 ConfigMap keys (env only — deployments reference the same `invenio-app-config` / `invenio-app-secrets`)
- Produces: pods that run the UGM image with local (NFS) file storage; storage layout consumed by Task 9's setup job (`$INVENIO_INSTANCE_PATH/data`)

- [ ] **Step 1: `invenio-data-pvc.yaml` — shared NFS-backed storage**

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: invenio-data
  namespace: invenio
  annotations:
    argocd.argoproj.io/sync-wave: "2"
spec:
  accessModes:
    - ReadWriteMany
  storageClassName: btd-nfs
  resources:
    requests:
      storage: 20Gi
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: invenio-archive
  namespace: invenio
  annotations:
    argocd.argoproj.io/sync-wave: "2"
spec:
  accessModes:
    - ReadWriteMany
  storageClassName: btd-nfs
  resources:
    requests:
      storage: 20Gi
```

- [ ] **Step 2: `invenio-deployment.yaml` — image, uwsgi args, mounts**

Changes:
- `containers[web].image: ghcr.io/vityasyyy/invenio-rdm-custom:latest` → `ghcr.io/vityasyyy/invenio-ugm:latest`
- `args` → the uwsgi UI app (serves both UI and API, matching the current single-service design):
```yaml
          args:
            - uwsgi
            - --ini
            - /opt/invenio/var/instance/uwsgi_ui.ini
```
- `volumeMounts` → add:
```yaml
          volumeMounts:
            - name: tmp
              mountPath: /tmp
            - name: data
              mountPath: /opt/invenio/var/instance/data
            - name: archive
              mountPath: /opt/invenio/var/instance/archive
```
- `volumes` → add:
```yaml
        - name: data
          persistentVolumeClaim:
            claimName: invenio-data
        - name: archive
          persistentVolumeClaim:
            claimName: invenio-archive
```
Keep: probes (`/ping` with Host header), `readOnlyRootFilesystem: true` (writes go to the PVC mounts and /tmp only), securityContext, resources, replicas 2.

- [ ] **Step 3: `invenio-worker-deployment.yaml` — image + mounts**

- image → `ghcr.io/vityasyyy/invenio-ugm:latest`
- `volumeMounts`/`volumes`: add the same `data` PVC (archive not needed by the worker, but mounting it is harmless — mount only `data`).
- Keep celery args unchanged (`celery -A invenio_app.celery worker ...`).

- [ ] **Step 4: `invenio-scheduler-deployment.yaml` — beat scheduler**

The UGM stack runs a dedicated `celery beat` (invenio_jobs scheduler); the current cluster has none. A separate singleton deployment (no HPA) is correct — beat must not scale:
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: invenio-scheduler
  namespace: invenio
  annotations:
    argocd.argoproj.io/sync-wave: "3"
  labels:
    app.kubernetes.io/name: invenio-scheduler
    app.kubernetes.io/part-of: invenio-rdm
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: invenio-scheduler
  template:
    metadata:
      labels:
        app.kubernetes.io/name: invenio-scheduler
        app.kubernetes.io/part-of: invenio-rdm
    spec:
      serviceAccountName: default
      securityContext:
        runAsNonRoot: true
        runAsUser: 1654
        runAsGroup: 1654
        fsGroup: 1654
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: scheduler
          image: ghcr.io/vityasyyy/invenio-ugm:latest
          imagePullPolicy: IfNotPresent
          args:
            - celery
            - -A
            - invenio_app.celery
            - beat
            - --scheduler
            - invenio_jobs.services.scheduler:RunScheduler
            - --loglevel=INFO
          envFrom:
            - configMapRef:
                name: invenio-app-config
            - secretRef:
                name: invenio-app-secrets
          volumeMounts:
            - name: tmp
              mountPath: /tmp
          livenessProbe:
            exec:
              command:
                - /bin/sh
                - -c
                - pgrep -f "celery.*beat" >/dev/null
            initialDelaySeconds: 30
            periodSeconds: 60
            failureThreshold: 3
          resources:
            requests:
              cpu: 100m
              memory: 256Mi
            limits:
              cpu: 500m
              memory: 1Gi
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop: ["ALL"]
            readOnlyRootFilesystem: true
            runAsNonRoot: true
            seccompProfile:
              type: RuntimeDefault
      volumes:
        - name: tmp
          emptyDir: {}
```

- [ ] **Step 5: `kustomization.yaml` — new resources + image name**

```yaml
resources:
  - namespace.yaml
  - app-config.yaml
  - namespace-governance.yaml
  - dependencies-external-services.yaml
  - app-sealed-secret.yaml
  - ghcr-pull-secret.yaml
  - serviceaccount.yaml
  - invenio-data-pvc.yaml
  - invenio-deployment.yaml
  - invenio-worker-deployment.yaml
  - invenio-scheduler-deployment.yaml
  - invenio-setup-job.yaml
  - invenio-ingressroute.yaml
  - invenio-hpa.yaml
  - invenio-pdb.yaml
  - argocd-log-reader.yaml

images:
  - name: ghcr.io/vityasyyy/invenio-ugm
    newTag: latest
```
(remove the old digest pin — the image-updater (Task 10) manages digests; the HPA `ignoreDifferences` in `invenio-bootstrap.yaml` stays as-is, and no ignore is needed for the scheduler since it has no HPA.)

- [ ] **Step 6: Validate renders**

```bash
./scripts/ci-render-manifests.sh
./scripts/ci-validate-selectors.sh
```
Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add k8s/apps/invenio/
git commit -m "feat: switch deployments to UGM image with local NFS storage and beat scheduler (#<issue>)"
```

---

## Task 9: Rewrite ConfigMap + Setup Job for the UGM Image

**Files:**
- Modify: `k8s/apps/invenio/app-config.yaml` (full replacement — see Step 1)
- Modify: `k8s/apps/invenio/invenio-setup-job.yaml` (script replacement — see Step 2)

**Interfaces:**
- Consumes: Task 7 cfg keys (`INVENIO_SEARCH_HOSTS` JSON, `INVENIO_CELERY_*`, `INVENIO_SITE_*`); Task 8 PVC layout (`$INVENIO_INSTANCE_PATH/data`)
- Produces: runtime config + idempotent PostSync setup for the UGM image (local files location, `rdm-` prefixed indices, custom fields, queues)

- [ ] **Step 1: Replace `app-config.yaml`**

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: invenio-app-config
  namespace: invenio
  annotations:
    argocd.argoproj.io/sync-wave: "0"
data:
  # Instance identity (UGM cfg reads these non-INVENIO_ prefixed vars)
  INSTANCE_DOMAIN: "invenio.vityasy.me"
  INSTANCE_NAME: "UGM Research Data Repository"
  INSTANCE_TAGLINE: "Share your research. Grow its impact. Keep it safe for the future."
  INSTANCE_PUBLISHER: "Universitas Gadjah Mada"
  INSTANCE_ADMIN_EMAIL: "admin@example.org"
  # Site URLs (deterministic overrides in the vendored invenio.cfg)
  INVENIO_SITE_UI_URL: "https://invenio.vityasy.me"
  INVENIO_SITE_API_URL: "https://api-invenio.vityasy.me"
  # NOTE: INVENIO_SQLALCHEMY_DATABASE_URI is NOT here — it is a secret and
  # already exists as a key of the `invenio-app-secrets` SealedSecret, which
  # every pod loads via secretRef. Leave it there; do not duplicate.
  # Search — IMPORTANT: must be valid JSON. String-form like UGM's compose
  INVENIO_SEARCH_HOSTS: '["invenio-search.invenio.svc.cluster.local:9200"]'
  # Redis URLs
  INVENIO_CACHE_REDIS_URL: "redis://invenio-redis.invenio.svc.cluster.local:6379/0"
  INVENIO_ACCOUNTS_SESSION_REDIS_URL: "redis://invenio-redis.invenio.svc.cluster.local:6379/1"
  INVENIO_COMMUNITIES_IDENTITIES_CACHE_REDIS_URL: "redis://invenio-redis.invenio.svc.cluster.local:6379/4"
  INVENIO_IIIF_CACHE_REDIS_URL: "redis://invenio-redis.invenio.svc.cluster.local:6379/0"
  INVENIO_RATELIMIT_STORAGE_URI: "redis://invenio-redis.invenio.svc.cluster.local:6379/3"
  # Celery broker/backend — cluster has no RabbitMQ, use Redis
  INVENIO_BROKER_URL: "redis://invenio-redis.invenio.svc.cluster.local:6379/0"
  INVENIO_CELERY_BROKER_URL: "redis://invenio-redis.invenio.svc.cluster.local:6379/0"
  INVENIO_CELERY_RESULT_BACKEND: "redis://invenio-redis.invenio.svc.cluster.local:6379/2"
  # Security / proxy
  INVENIO_APP_ENABLE_SECURE_HEADERS: "False"
  INVENIO_JSONSCHEMAS_HOST: "invenio.vityasy.me"
  INVENIO_WSGI_PROXIES: "1"
```
Note: the `INVENIO_DB_*`/`INVENIO_S3_*` keys are intentionally removed — the UGM image reads the SQLAlchemy URI directly from the sealed secret, and S3/MinIO is no longer used for app files.

- [ ] **Step 2: Replace the setup job script (idempotent, PostSync)**

The UGM `bootstrap.sh` sequence adapted for K8s (runs on every sync; every step tolerant of "already exists"). Replace the `command` block in `invenio-setup-job.yaml` with:
```bash
set -e

echo "=== InvenioRDM UGM Setup (PostSync Hook) ==="

python3 - <<'PY'
import socket, time, urllib.request

def wait_tcp(host, port, name):
    for i in range(60):
        try:
            socket.create_connection((host, port), timeout=3).close()
            print(f"{name} ready"); return
        except Exception:
            print(f"Waiting for {name}... ({i+1}/60)"); time.sleep(5)
    raise RuntimeError(f"{name} not available after 5 minutes")

def wait_http(url, name):
    for i in range(60):
        try:
            urllib.request.urlopen(url, timeout=3)
            print(f"{name} ready"); return
        except Exception:
            print(f"Waiting for {name}... ({i+1}/60)"); time.sleep(5)
    raise RuntimeError(f"{name} not available after 5 minutes")

wait_tcp("invenio-postgresql.invenio.svc.cluster.local", 5432, "PostgreSQL")
wait_tcp("invenio-redis.invenio.svc.cluster.local", 6379, "Redis")
wait_http("http://invenio-search.invenio.svc.cluster.local:9200/_cluster/health", "OpenSearch")
PY

echo "[1/9] Database tables (idempotent)..."
invenio db create 2>&1 || echo "Tables may already exist, continuing."
invenio alembic upgrade 2>&1
echo "Migrations done."

echo "[2/9] Default files location (local, on the NFS PVC)..."
invenio files location create --default default-location "$INVENIO_INSTANCE_PATH/data" 2>&1 \
  || echo "File location may already exist, continuing."

echo "[3/9] Admin role and permissions..."
invenio roles create admin 2>&1 || echo "Role may already exist."
invenio access allow superuser-access role admin 2>&1 || echo "Permission may already exist."

echo "[4/9] Search index (rdm- prefix from UGM config)..."
invenio index init 2>&1 || echo "Indices may already exist, continuing."

echo "[5/9] Custom fields..."
invenio rdm-records custom-fields init 2>&1 || echo "Records custom fields may already exist."
invenio communities custom-fields init 2>&1 || echo "Communities custom fields may already exist."

echo "[6/9] Fixtures (general + vocabularies)..."
invenio rdm fixtures 2>&1 || true
invenio rdm-records fixtures 2>&1 || true

echo "[7/9] Message queues..."
invenio queues declare 2>&1 || echo "Queues may already be declared."

echo "[8/9] Rebuild search index for existing records..."
invenio rdm-records rebuild-index 2>&1 || echo "No existing records to rebuild."

echo "[9/9] Setup complete."
```
Remove: the boto3 bucket-verification step (boto3 does not exist in the UGM image) and the `s3://invenio-rdm/files/` location creation. Keep the image ref change to `ghcr.io/vityasyyy/invenio-ugm:latest` for Task 8 parity.

- [ ] **Step 3: Validate renders**

```bash
./scripts/ci-render-manifests.sh
./scripts/ci-validate-selectors.sh
```

- [ ] **Step 4: Commit**

```bash
git add k8s/apps/invenio/app-config.yaml k8s/apps/invenio/invenio-setup-job.yaml
git commit -m "feat: configure UGM image runtime env and idempotent setup job (#<issue>)"
```

---

## Task 10: Update CI Build Workflow and Image Updater

**Files:**
- Modify: `.github/workflows/build-image.yaml` (image name `invenio-ugm`, Dockerfile path, verify-job imports)
- Modify: `k8s/infra/argocd-image-updater/image-updater-cr.yaml` (image name + manifest target)
- Modify: `argocd/apps/invenio-bootstrap.yaml` (ignoreDifferences stays; no change expected — verify)

**Interfaces:**
- Consumes: Task 7 image + Task 8 kustomization image name
- Produces: automated GHCR builds of `ghcr.io/vityasyyy/invenio-ugm` on merge to main; image-updater keeps the kustomization digest fresh

- [ ] **Step 1: `build-image.yaml` — new image name and paths**

```yaml
env:
  REGISTRY: ghcr.io
  IMAGE_NAME: ${{ github.repository_owner }}/invenio-ugm
```
- `paths:` watch `docker/ugm/**`, `site/**`, `templates/**`, `static/**`, `app_data/**`, `translations/**`, `assets/**`, `Pipfile*` (update the existing path list; remove the stale `docker/invenio/**`).
- Build step: `file: docker/ugm/Dockerfile`.
- Verify job — replace the invenio-s3/boto3 import smoke test (they are no longer installed):
```yaml
      - name: Test image starts
        run: |
          IMAGE="${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:latest"
          docker run --rm $IMAGE python -c \
            "import invenio_app; print('Invenio app imported successfully'); \
             import invenio_app_rdm; print('invenio-app-rdm', invenio_app_rdm.__version__)"
          docker run --rm --entrypoint /bin/bash $IMAGE -c \
            "uwsgi --version && id -u && id -g"
```
Expected: prints version, `1654`/`1654`.

- [ ] **Step 2: `image-updater-cr.yaml` — new image name**

```yaml
      images:
        - alias: invenio
          imageName: ghcr.io/vityasyyy/invenio-ugm:latest
          commonUpdateSettings:
            updateStrategy: digest
          manifestTargets:
            kustomize:
              name: ghcr.io/vityasyyy/invenio-ugm
```

- [ ] **Step 3: Validate + commit**

```bash
./scripts/ci-render-manifests.sh
git add .github/workflows/build-image.yaml k8s/infra/argocd-image-updater/image-updater-cr.yaml
git commit -m "feat: build and auto-update UGM image via CI and image-updater (#<issue>)"
```

- [ ] **Step 4: Open the T3 PR (combining Tasks 6–10) and merge after review**

```bash
gh pr create --title "[T3] Migrate Invenio image to UGM-personalized instance" --squash --body "Closes #<issue> ... (risk assessment + rollback)"
gh pr merge <number> --squash --delete-branch
```
Wait for CI to pass and ArgoCD to sync.

---

## Task 11: Clean-Slate Deploy and End-to-End Verification

**Files:** none (cluster operations)

**Interfaces:**
- Consumes: Tasks 8–10 merged (new image + config live or ready)
- Produces: UGM instance verified end-to-end at `invenio.vityasy.me`

- [ ] **Step 1: Optional clean slate (recommended — fresh start is acceptable)**

Wipe the old schema and indices so the new `rdm-`-prefixed indices start clean:
```bash
PGPOD=$(kubectl -n database get pods -l role=primary -o jsonpath='{.items[0].metadata.name}')
kubectl -n database exec "$PGPOD" -- psql -U invenio -d invenio -c \
  "DROP SCHEMA public CASCADE; CREATE SCHEMA public AUTHORIZATION invenio;"
kubectl -n search exec deploy/opensearch-cluster-master -- \
  curl -s -XDELETE 'http://localhost:9200/*' || true
```
Verify deletion: `kubectl -n search exec ... -- curl -s 'http://localhost:9200/_cat/indices'` → empty.
(If the setup hook already ran before this wipe, simply trigger another sync afterwards — the setup job is idempotent.)

- [ ] **Step 2: Sync and wait**

```bash
kubectl -n argocd rollout restart deploy/argocd-application-controller  # pick up new manifests
kubectl -n argocd get application invenio-bootstrap -w
```
Expected: `Synced`/`Healthy`; `invenio-web`/`invenio-worker`/`invenio-scheduler` all `Running`; the setup PostSync hook `Succeeded`:
```bash
kubectl -n invenio get jobs,pods -o wide | grep -E "invenio-setup|invenio-web|invenio-worker|invenio-scheduler"
kubectl -n invenio logs job/invenio-setup --tail=40
```
Expected: log ends with `[9/9] Setup complete.`

- [ ] **Step 3: Verify endpoints + UGM branding**

```bash
curl -s -m 15 -o /dev/null -w "UI: %{http_code}\n" https://invenio.vityasy.me/
curl -s -m 15 https://invenio.vityasy.me/ | grep -io "UGM Research Data Repository\|Gadjah Mada" | head -3
curl -s -m 15 -o /dev/null -w "API: %{http_code}\n" https://api-invenio.vityasy.me/api
```
Expected: UI `200` containing UGM branding; API `200`.

- [ ] **Step 4: End-to-end record + upload + search round-trip**

Create the smoke user (same as Task 4), then:
```bash
TOKEN=$(curl -s -m 20 -X POST https://invenio.vityasy.me/api/accounts/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"smoke-test@example.org","password":"SmokeTest!234"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

PID=$(curl -s -m 30 -X POST 'https://invenio.vityasy.me/api/records?expand=1' \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"access_right":"public","files":{"enabled":true},"metadata":{"title":"ugm smoke test","upload_type":{"type":"publication"},"publication_date":"2026-08-14"},"rights":[{"id":"cc-by-4.0"}]}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
echo "PID=$PID"

echo "hello ugm $(date +%s)" > /tmp/ugm-smoke.txt
curl -s -m 30 -X POST "https://invenio.vityasy.me/api/records/$PID/draft/files" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"name":"ugm-smoke.txt"}'
curl -s -m 120 -X PUT "https://invenio.vityasy.me/api/records/$PID/draft/files/ugm-smoke.txt/content" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/octet-stream' --data-binary @/tmp/ugm-smoke.txt
curl -s -m 30 -X POST "https://invenio.vityasy.me/api/records/$PID/draft/actions/publish" \
  -H "Authorization: Bearer $TOKEN" -o /dev/null -w "publish: %{http_code}\n"

sleep 15
curl -s -m 30 "https://invenio.vityasy.me/api/records?q=title:%22ugm%20smoke%20test%22&size=1" \
  -H "Authorization: Bearer $TOKEN" | python3 -c 'import sys,json;d=json.load(sys.stdin);print("hits:", d["hits"]["total"])'
curl -s -m 30 -o /tmp/downloaded.txt -w "download: %{http_code}\n" \
  "https://invenio.vityasy.me/api/records/$PID/files/ugm-smoke.txt/content"
diff /tmp/ugm-smoke.txt /tmp/downloaded.txt && echo "FILE ROUND-TRIP OK"
```
Expected: publish `200` (the historical 500 must not appear), `hits: >= 1`, `download: 200`, `FILE ROUND-TRIP OK` (proves NFS PVC storage works through web + worker).

- [ ] **Step 5: Verify worker + scheduler + metrics side-effects**

```bash
kubectl -n invenio logs deploy/invenio-worker --tail=30 | tail -10   # indexing tasks completed
kubectl -n invenio logs deploy/invenio-scheduler --tail=10 | tail -5 # beat started, no crash loop
kubectl -n invenio get hpa,deploy | grep -E "invenio-web|invenio-worker|invenio-scheduler"
```
Expected: worker processed indexing tasks; scheduler logs show the scheduler startup line; no CrashLoopBackOff.

- [ ] **Step 6: Record verification results in the migration issue**

Check off the acceptance criteria in the T3 issue with the actual outputs.

---

## Task 12: Cleanup and Documentation

**Files:**
- Delete: `DEBUG_PROGRESS.md` (saga resolved)
- Modify: `README.md` (image name, storage notes, Access table unchanged, quick-start image refs), `docker/README.md` if it exists
- Optional delete: `k8s/infra/minio/create-bucket-job.yaml` bucket `invenio-rdm`/`invenio-rdm-uploads` entries (only if MinIO buckets are no longer wanted — keep `velero-backups`/`invenio-rdm-backups` if CNPG backups still use MinIO; **verify before removing**)
- Optional: remove unused `INVENIO_S3_*` keys from `k8s/apps/invenio/app-sealed-secret.yaml` via `./scripts/generate-sealed-secrets.sh invenio` (requires kubeseal + cluster access; safe to defer)

**Interfaces:**
- Consumes: Task 11 verified migration
- Produces: repo fully reflecting the UGM state; no leftover debug/S3 references

- [ ] **Step 1: Delete DEBUG_PROGRESS.md and update README**

```bash
git rm DEBUG_PROGRESS.md
```
README updates:
- "Access & Services" table stays (`invenio.vityasy.me` unchanged).
- `docker/invenio/` references → `docker/ugm/` (Quick Start/Docker section, if referenced).
- Image name `ghcr.io/vityasyyy/invenio-rdm-custom` → `ghcr.io/vityasyyy/invenio-ugm` anywhere it appears.

- [ ] **Step 2: Grep for stale references**

```bash
grep -rn "invenio-rdm-custom\|docker/invenio\|invenio_s3\|s3://invenio-rdm" --include="*.yaml" --include="*.yml" --include="*.md" --include="*.sh" . | grep -v node_modules
```
Fix every hit (except intentional history).

- [ ] **Step 3: Final render validation + PR**

```bash
./scripts/ci-render-manifests.sh && ./scripts/ci-validate-selectors.sh
git checkout -b fix/<issue>-ugm-cleanup
git add -A && git commit -m "docs: clean up debug artifacts and document UGM migration (#<issue>)"
gh pr create --title "[T1] UGM migration cleanup" --squash --body "Closes #<issue>"
gh pr merge <number> --squash --delete-branch
```

- [ ] **Step 4: Final cluster state confirmation**

```bash
kubectl -n argocd get applications | awk 'NR==1 || $3=="Synced" && $4=="Healthy" || $2=="OutOfSync" || $2=="Degraded"'
curl -s -o /dev/null -w "UI %{http_code} " https://invenio.vityasy.me/ && curl -s -o /dev/null -w "API %{http_code}\n" https://api-invenio.vityasy.me/api
```
Expected: all 16 apps `Synced`/`Healthy`; UI and API `200`. Baseline = recovered, UGM migration = complete.

---

# Rollback Plan (T3 requirement, applies to the migration PR)

1. **Revert via git**: `git revert` the migration PR merge (`feat: ... UGM ...`). ArgoCD self-heals back to the `invenio-rdm-custom` image, gunicorn args, and S3 storage location within one sync cycle (~30s–2min).
2. **If PVC mounts conflict with the old image**: the old image ignores the `data`/`archive` PVCs (they simply go unused) — no manifest churn needed for rollback beyond the revert.
3. **If the DB schema was wiped (Task 11 Step 1)**: old records are gone (accepted — fresh start). Restore only if a CNPG/Velero backup snapshot exists: `kubectl -n velero` + CNPG `backup` restore procedure from README "Backup & Restore".
4. **Verify rollback**: repeat Task 4 smoke test (draft → upload → publish) against the old image; `kubectl -n argocd get applications` all Healthy.

# Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| `INVENIO_SEARCH_HOSTS` JSON malformed → record creation 500s again | High | String-form `["host:9200"]` (proven in UGM compose); Task 11 Step 4 smoke test before declaring done |
| uwsgi `http` socket regression vs. probes (`/ping`, Host header) | High | Patched ini verified locally (Task 7 Step 4) and by startup probe in Task 11 |
| Non-root uid 1654 missing in built image → pods CrashLoop on `runAsNonRoot` | High | Dockerfile `USER invenio` (uid 1654); CI verify job checks `id -u`/`id -g` |
| NFS PVC (RWX) not writable by uid 1654 → upload/download failures | Medium | `fsGroup: 1654` already set; Task 11 Step 4 file round-trip test |
| `celery beat` duplicated (worker `--beat` + scheduler) → duplicate jobs | Low | Only the dedicated scheduler runs beat; worker args unchanged (no `--beat`) |
| Redis as broker/backend semantics differ from UGM's RabbitMQ | Medium | Explicit `INVENIO_CELERY_BROKER_URL`/`RESULT_BACKEND`/`BROKER_URL` set; worker startup probe verifies connectivity |
| Image build time/registry auth for `registry.cern.ch` base image | Low | Same base as upstream UGM (proven); CI has no new credentials requirement |
| MinIO buckets removal breaks CNPG/Velero backups | Medium | Task 12 defers bucket removal until verified; only drop app-file buckets if unused |
| ArgoCD image-updater writes conflicting digest into kustomization | Low | Rename CR to new image; digest strategy unchanged |

# Acceptance Criteria (from the T3 issue)

- [ ] All 16 ArgoCD apps `Synced` + `Healthy` after each phase
- [ ] `https://invenio.vityasy.me/` returns 200 with UGM branding ("UGM Research Data Repository")
- [ ] `POST /api/records?expand=1` returns 200 (historical 500 gone) — verified via smoke test
- [ ] File upload → publish → search → download round-trip passes (NFS-backed local storage)
- [ ] `invenio-scheduler` (celery beat) runs as a healthy singleton
- [ ] CI on the migration PR passes: yamllint, kustomize render, kubeconform, selectors, gitleaks
- [ ] GHCR image `ghcr.io/vityasyyy/invenio-ugm` built and pushed from this repo; image-updater tracks it
- [ ] No stale `invenio-rdm-custom`, `invenio_s3`, `s3://invenio-rdm`, or debug-artifact references remain
- [ ] ArgoCD admin password rotated; new password stored only in `secrets/` (gitignored)
