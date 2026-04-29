# Infrastructure GitOps for Kubernetes

GitOps infrastructure repository for deploying and managing ArgoCD, Sealed Secrets, and Cloudflare Tunnel on a Rancher-managed Kubernetes cluster.

## Scope

This repository contains infrastructure resources plus a GitOps bootstrap scaffold for Invenio RDM (namespace wiring, dependency integration points, ingress, and sealed-secret templates).

**Components:**
- ArgoCD (GitOps controller)
- Sealed Secrets controller (secret encryption for Git)
- Cloudflare Tunnel (outbound-only external access, DDoS protection)

## Quick Start

```bash
# 1. Verify cluster prerequisites
./scripts/verify-infra.sh

# 2. Bootstrap ArgoCD (installs ArgoCD only)
./scripts/bootstrap-infra.sh
# IMPORTANT: ArgoCD will automatically sync all infrastructure apps from Git

# 3. Activate Cloudflare Tunnel (creates tunnel token secret)
./external-lb/scripts/bootstrap-dev.sh

# 4. Verify everything is running
./scripts/verify-infra.sh

# 5. Log into ArgoCD and change default password
#    URL: https://argocd.vityasy.me
#    Username: admin
#    Password: (from bootstrap-infra.sh output)
```

## Sync Waves

ArgoCD syncs infrastructure in this order:

| Wave | App | Purpose |
|------|-----|---------|
| -5 | security-policies | NetworkPolicies, LimitRanges, ResourceQuotas, PodSecurityAdmission |
| -4 | traefik | Ingress controller |
| -4 | cloudflared | Cloudflare Tunnel DaemonSet |
| -3 | sealed-secrets | Secret encryption controller |
| -3 | argocd-self | ArgoCD self-management |
| -2 | cert-manager | TLS certificate management |
| 2 | velero | Cluster backups |
| 5 | monitoring | Prometheus/Grafana |
| 7 | invenio-bootstrap | Invenio namespace bootstrap manifests (dependencies, secrets template, ingress, app scaffold) |

**Manual steps (not ArgoCD-managed):**
- Cloudflare Terraform (dynamic token generation via `bootstrap-dev.sh`)

## Architecture

```
                              INTERNET
                                 |
                          [Users / Browsers]
                                 |
                                 v
                    +---------------------------+
                    |    CLOUDFLARE EDGE CDN    |
                    |  (TLS termination, DDoS,  |
                    |   WAF, DNS resolution)    |
                    +---------------------------+
                                 |
                      Cloudflare Tunnel (outbound only)
                                 |
                                 v
 +---------------------------------------------------------------+
 |                  KUBERNETES CLUSTER                           |
 |                (Rancher-managed)                            |
 |                                                               |
 |  ArgoCD sync waves (auto-synced from Git):                   |
 |    Wave -5: security-policies (NetworkPolicies, quotas)      |
 |    Wave -4: traefik + cloudflared                             |
 |    Wave -3: sealed-secrets + argocd-self                      |
 |    Wave -2: cert-manager                                      |
 |    Wave  2: velero                                            |
 |    Wave  5: monitoring (Prometheus/Grafana)                   |
 |                                                               |
 |  Manual setup (dynamic secrets):                              |
 |    bootstrap-infra.sh → ArgoCD install                        |
 |    bootstrap-dev.sh   → Cloudflare tunnel token               |
 +---------------------------------------------------------------+

SECRETS FLOW:
  1. Sealed Secrets controller deploys (wave -3)
  2. Controller generates key pair (stored in kube-system secret)
  3. Public key exported for sealing secrets:
       kubectl get secret -n kube-system sealed-secrets-key* \
         -o jsonpath='{.data.tls\.crt}' | base64 -d > secrets/sealed-secrets-public.pem
  4. Applications generate SealedSecret CRDs using public key
  5. Controller decrypts SealedSecrets into K8s Secrets
  6. BACK UP THE PRIVATE KEY! (see SETUP.md for disaster recovery)
```

## Disaster Recovery

The Sealed Secrets private key (`secrets/sealed-secrets-private.pem`) is critical for disaster recovery:

- If the cluster is destroyed, you need this key to decrypt existing SealedSecrets
- Without it, all encrypted secrets are permanently lost
- Store it in a password manager, encrypted USB, or secure vault

See [SETUP.md](SETUP.md) for detailed recovery procedures.

## Security Notes

- **Zero inbound ports**: Cloudflare Tunnel = outbound-only connection, no firewall holes
- **DDoS protection**: Traffic flows through Cloudflare's global network
- **Encrypted secrets**: SealedSecrets are safe to commit to Git
- **Non-root pods**: All infrastructure pods run as non-root with dropped capabilities

## Secrets policy

- `k8s/apps/invenio/app-sealed-secret.yaml` stores only ciphertext in `spec.encryptedData` for: `SQLALCHEMY_DATABASE_URI`, `CACHE_REDIS_URL`, `OPENSEARCH_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `INVENIO_SECRET_KEY`.
- Plaintext for those keys exists only during local sealing (`kubectl create secret ... --from-literal=... | kubeseal ...`) and must never be committed.
- Rotate by generating new plaintext values locally, resealing with `kubeseal`, committing only the updated sealed manifest, then restarting workloads as needed.
- Invenio currently consumes Redis via `invenio-redis` `ExternalName` → `redis-master.redis.svc.cluster.local`; this is an external dependency pattern, not ArgoCD Redis reuse.
- MinIO may be a shared instance (e.g., Invenio + Velero), but credentials should be scoped per consumer where possible (least privilege), avoiding broad/root credential reuse.
- ArgoCD Redis is internal control-plane state for ArgoCD and must not be reused by Invenio workloads.

## Access and Authentication Map

| Service | URL / Endpoint | Auth source | Owner persona | Health check command |
|------|------|------|------|------|
| ArgoCD UI | `https://argocd.vityasy.me` | `argocd-initial-admin-secret` (cluster-generated Secret; not a SealedSecret) | Platform operator | `curl -s -o /dev/null -w "%{http_code}\n" https://argocd.vityasy.me` |
| Grafana UI | `https://grafana.vityasy.me` | `SealedSecret/monitoring-grafana` → Secret `monitoring-grafana` | Observability operator | `curl -s -o /dev/null -w "%{http_code}\n" https://grafana.vityasy.me` |
| MinIO S3 API | `minio.minio.svc.cluster.local:9000` (internal only; no public route) | `SealedSecret/minio-credentials` → Secret `minio-credentials` | Backup/storage operator | `kubectl -n minio get svc minio` |
| MinIO Console | `https://minio-console.vityasy.me` (via Traefik) and `minio-console.minio.svc.cluster.local:9001` (internal) | `SealedSecret/minio-credentials` → Secret `minio-credentials` | Backup/storage operator | `kubectl -n minio get svc minio-console` |
| Velero controller | Namespace `velero` (CRDs + controller; no public HTTP route) | `SealedSecret/velero-credentials` → Secret `velero-credentials` | Platform operator | `kubectl -n velero get backupstoragelocation,schedule` |
| Cloudflare tunnel connector | `*.vityasy.me` tunnel routing to Traefik | `SealedSecret/cloudflared-credentials` → Secret `cloudflared-credentials` | Platform operator | `kubectl -n kube-system get ds cloudflared` |

## Hostname Mapping Inventory

- **DNS/tunnel wildcard in place**: `*.vityasy.me` → `http://traefik.traefik.svc.cluster.local:80` (`external-lb/terraform/envs/dev/main.tf`)
- **Hostnames currently backed by Traefik IngressRoute:**
  - `argocd.vityasy.me` → `argocd/argocd-server:8080`
  - `grafana.vityasy.me` → `monitoring/monitoring-grafana:80`
- **External ingress mappings:**
  - `minio-console.vityasy.me` → `minio/minio-console:9001` (web console)
- **Internal-only service endpoints (not exposed publicly):**
  - `minio.minio.svc.cluster.local:9000` → `minio/minio:9000` (S3 API for in-cluster clients like Velero)
- **Invenio routes (bootstrap scaffold):**
  - `invenio.vityasy.me` → `invenio/invenio-web:8000`
  - `api-invenio.vityasy.me` → `invenio/invenio-web:8000`

## Invenio Bootstrap (GitOps scaffold)

- ArgoCD app: `argocd/apps/invenio-bootstrap.yaml` (wave `7`)
- Workload manifests: `k8s/apps/invenio/`
- Dependency integration pattern: `ExternalName` Services (`invenio-postgresql`, `invenio-redis`, `invenio-search`) pointing to existing service backends.
- Secret model: `SealedSecret` template (`invenio-app-secrets`) generated by `./scripts/generate-sealed-secrets.sh invenio`; `spec.encryptedData` is ciphertext only, and plaintext lives only under the gitignored `secrets/` directory.
- Rotation model: override env vars (for example `INVENIO_SECRET_KEY` or `INVENIO_S3_SECRET_ACCESS_KEY`) and rerun the generator, then commit only the sealed YAML.
- MinIO usage: the Invenio setup job ensures required buckets exist (`invenio-rdm`, `invenio-rdm-uploads`, `invenio-rdm-backups`) before the web deployment starts. If you change bucket names, update both `k8s/apps/invenio/invenio-setup-job.yaml` and `k8s/apps/invenio/app-config.yaml`.
- Redis usage: ArgoCD Redis is control-plane storage only and is not reused by Invenio.
- Network policy compatibility:
  - `k8s/infra/security/network-policies/invenio-netpol.yaml` allows egress to DNS, DB, Redis, search, MinIO.
  - `k8s/infra/security/network-policies/minio-allow.yaml` allows ingress from `invenio` to MinIO S3 API (`9000`).

## Operations Runbooks (Day-1 / Day-2)

### Day-1: infra deploy → first Invenio bootstrap rollout

```bash
# 1) Bootstrap base infrastructure and tunnel
./scripts/bootstrap-infra.sh
./external-lb/scripts/bootstrap-dev.sh
./scripts/verify-infra.sh

# 2) Confirm ArgoCD infra apps are healthy
kubectl -n argocd get applications

# 3) Apply/bootstrap Invenio app definition (if not already present)
kubectl apply -f argocd/apps/invenio-bootstrap.yaml

# 4) Confirm Invenio bootstrap resources and rollout
kubectl -n argocd get application invenio-bootstrap
kubectl -n invenio get sealedsecret invenio-app-secrets
kubectl -n invenio get deploy invenio-web
kubectl -n invenio scale deploy/invenio-web --replicas=1
kubectl -n invenio rollout status deploy/invenio-web --timeout=180s
kubectl -n invenio get ingressroute invenio-ui invenio-api
```

### Day-2 operations

#### Upgrade flow (GitOps)

```bash
# 1) Update versions/manifests (examples)
#   - argocd/apps/*.yaml (chart targetRevision/path)
#   - k8s/infra/*/values.yaml
#   - k8s/apps/invenio/*.yaml

# 2) Commit + push, then verify/sync
kubectl -n argocd get applications
argocd app sync invenio-bootstrap
argocd app wait invenio-bootstrap --health --timeout 300
# if invenio-web replicas > 0:
kubectl -n invenio rollout status deploy/invenio-web --timeout=180s
```

#### Rollback

```bash
# Preferred: Git revert and resync
git revert <bad-commit-sha>
git push
argocd app sync invenio-bootstrap
argocd app wait invenio-bootstrap --health --timeout 300

# Emergency app-level rollback
argocd app history invenio-bootstrap
argocd app rollback invenio-bootstrap <history-id>
```

#### Secret rotation

```bash
# Re-seal only the Invenio secret bundle
INVENIO_SECRET_KEY="$(openssl rand -hex 32)" \
  ./scripts/generate-sealed-secrets.sh invenio

# Or reseal the shared infra creds set (MinIO / Grafana / Velero)
./scripts/generate-sealed-secrets.sh minio grafana velero
```

Commit and push the sealed YAML; ArgoCD will sync it from Git.

For infra credentials, rotate and reseal:
- `k8s/infra/minio/minio-credentials-secret.yaml`
- `k8s/infra/velero/velero-credentials-secret.yaml`
- `k8s/infra/monitoring/grafana-admin-secret.yaml`

#### Backup/restore checks

```bash
# Backup freshness and storage health
kubectl -n velero get backupstoragelocation default -o jsonpath='{.status.phase}{"\n"}'
kubectl -n velero get schedule weekly-infra-backup
kubectl -n velero get backups -l velero.io/schedule-name=weekly-infra-backup --sort-by=.status.completionTimestamp

# Spot-check latest backup details
velero backup describe <latest-backup-name> --details

# Restore drill (see detailed canary flow above)
velero restore get

# Confirm scheduled backup scope still includes invenio
kubectl -n velero get schedule weekly-infra-backup -o jsonpath='{.spec.template.includedNamespaces}{"\n"}'
```

#### Incident triage commands

```bash
# Control plane and app health
kubectl -n argocd get applications
kubectl get pods -A --field-selector=status.phase!=Running

# Invenio namespace quick triage
kubectl -n invenio get all
kubectl -n invenio describe deploy invenio-web
kubectl -n invenio logs deploy/invenio-web --tail=200

# Dependency checks
kubectl -n invenio get svc invenio-postgresql invenio-redis invenio-search
kubectl -n minio get svc minio

# Tunnel/ingress checks
kubectl -n kube-system get ds cloudflared
kubectl -n kube-system logs -l app=cloudflared --tail=100
kubectl -n traefik get pods,svc
```

#### Upload returns 404 or fails

1. Check MinIO buckets exist:
   ```bash
   kubectl -n invenio exec deployment/invenio-web -- python3 -c "
   import boto3
   s3 = boto3.client('s3', endpoint_url='http://minio.minio.svc.cluster.local:9000')
   print([b['Name'] for b in s3.list_buckets()['Buckets']])
   ```
   Expected: `['invenio-rdm', 'invenio-rdm-uploads', 'invenio-rdm-backups', 'velero-backups']`

2. Check file location is configured in DB:
   ```bash
   kubectl -n database exec -it postgres-1 -- psql -U app-user invenio -c "SELECT * FROM files_location;"
   ```
   Expected: `default-location` with URI `s3://invenio-rdm/files/`

3. Check worker OpenSearch connectivity:
   ```bash
   kubectl -n invenio logs deployment/invenio-worker --tail=50 | grep -i error
   ```
   If you see `Connection refused` to OpenSearch, restart the worker: `kubectl -n invenio rollout restart deployment/invenio-worker`

## Routine Maintenance Checklist

- **Daily**
  - `kubectl -n argocd get applications` (all Healthy/Synced).
  - `kubectl get pods -A --field-selector=status.phase!=Running`.
- **Weekly**
  - `kubectl -n velero get backups -l velero.io/schedule-name=weekly-infra-backup --sort-by=.status.completionTimestamp`.
  - Review Grafana dashboards (`Cluster Health`, `Traefik`, `Velero`, `MinIO`, `Invenio Operations`).
  - Confirm Invenio route health: `curl -s -o /dev/null -w "%{http_code}\n" https://invenio.vityasy.me`.
- **Monthly**
  - Run Velero restore drill (canary ConfigMap procedure above).
  - Rotate at least one credential set (Invenio/MinIO/Velero/Grafana) and verify rollout.
  - Review ArgoCD app revisions and prune stale resources: `argocd app list`.

## Using What Is Deployed Now

### ArgoCD

```bash
# Get current admin password (if still using bootstrap admin account)
kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath='{.data.password}' | base64 -d && echo
```

Open `https://argocd.vityasy.me`, log in as `admin`, then rotate the password.

### Grafana

```bash
# Get Grafana admin credentials from the generated Secret
kubectl -n monitoring get secret monitoring-grafana \
  -o jsonpath='{.data.admin-user}' | base64 -d && echo
kubectl -n monitoring get secret monitoring-grafana \
  -o jsonpath='{.data.admin-password}' | base64 -d && echo
```

Open `https://grafana.vityasy.me`.

Pre-provisioned dashboards are GitOps-managed via `k8s/infra/monitoring/grafana-dashboards.yaml`:
- **Cluster Health Overview** (Kubernetes folder)
- **Traefik Traffic & Errors** (Traefik folder)
- **Velero Backups** (Velero folder)
- **MinIO Capacity & Availability** (MinIO folder)
- **Invenio Operations** (Invenio folder; availability, restarts, proxy errors, PVC usage)

Metric compatibility notes:
- Traefik dashboard expects `traefik_service_*` metrics from Traefik Prometheus integration.
- Velero dashboard expects `velero_backup_*` metrics (from Velero metrics scraping).
- MinIO dashboard expects `minio_cluster_capacity_*` and `minio_s3_requests_*` metrics.
- Invenio dashboard combines `up`, kube-state-metrics (`kube_deployment_*`, `kube_pod_container_status_restarts_total`), Traefik request metrics, and `kubelet_volume_stats_*`.
- If any metrics are absent, panels will show *No data*; verify scrape configuration for those components in Prometheus.

### MinIO

```bash
# Console (web UI)
kubectl -n minio port-forward svc/minio-console 9001:9001

# API endpoint for S3-compatible tooling
kubectl -n minio port-forward svc/minio 9000:9000
```

Use `https://minio-console.vityasy.me` for the web console via Traefik/Cloudflare.
For API access, keep using the internal endpoint (`minio.minio.svc.cluster.local:9000`) or local port-forward (`http://localhost:9000`).
Credentials come from Secret `minio-credentials`.

### Velero

```bash
# Check backup storage location and schedule status
kubectl -n velero get backupstoragelocation,schedule
kubectl -n velero get backupstoragelocation default -o jsonpath='{.status.phase}{"\n"}'
```

#### Manual backup trigger flow (operator runbook)

```bash
# 1) Trigger an on-demand infra backup (keeps scheduled backup unchanged)
BACKUP_NAME="manual-infra-$(date +%Y%m%d-%H%M%S)"
velero backup create "$BACKUP_NAME" \
  --include-namespaces argocd,traefik,monitoring,minio,invenio,velero,cert-manager \
  --storage-location default \
  --ttl 168h \
  --wait

# 2) Verify completion and inspect warnings/errors
velero backup describe "$BACKUP_NAME" --details
velero backup logs "$BACKUP_NAME"
```

#### Restore drill procedure (safe canary object)

```bash
# 1) Create a canary ConfigMap in velero namespace
DRILL_ID="$(date +%Y%m%d-%H%M%S)"
kubectl -n velero create configmap restore-drill-canary \
  --from-literal=drill-id="$DRILL_ID" \
  --from-literal=source=velero-readiness \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n velero label configmap restore-drill-canary backup-drill=velero --overwrite

# 2) Back up only the canary object
DRILL_BACKUP="restore-drill-${DRILL_ID}"
velero backup create "$DRILL_BACKUP" \
  --include-namespaces velero \
  --include-resources configmaps \
  --selector backup-drill=velero \
  --storage-location default \
  --ttl 168h \
  --wait

# 3) Simulate loss of object, then restore from backup
kubectl -n velero delete configmap restore-drill-canary
DRILL_RESTORE="restore-drill-${DRILL_ID}"
velero restore create "$DRILL_RESTORE" --from-backup "$DRILL_BACKUP" --wait

# 4) Verify restored data
kubectl -n velero get configmap restore-drill-canary -o jsonpath='{.data.drill-id}{"\n"}'
velero restore describe "$DRILL_RESTORE" --details
```

Restore drill success criteria:
- Backup phase is `Completed` with no unexpected errors.
- Restore phase is `Completed`.
- `restore-drill-canary` exists again in `velero` namespace and `drill-id` matches.

#### Invenio restore-validation guidance

Use this after backup policy changes or dependency updates affecting `invenio`.

```bash
# 1) Create Invenio canary ConfigMap
DRILL_ID="$(date +%Y%m%d-%H%M%S)"
kubectl -n invenio create configmap invenio-restore-drill-canary \
  --from-literal=drill-id="$DRILL_ID" \
  --from-literal=source=invenio-restore-validation \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n invenio label configmap invenio-restore-drill-canary backup-drill=invenio --overwrite

# 2) Back up only the canary object in invenio namespace
DRILL_BACKUP="invenio-restore-drill-${DRILL_ID}"
velero backup create "$DRILL_BACKUP" \
  --include-namespaces invenio \
  --include-resources configmaps \
  --selector backup-drill=invenio \
  --storage-location default \
  --ttl 168h \
  --wait

# 3) Simulate loss and restore
kubectl -n invenio delete configmap invenio-restore-drill-canary
DRILL_RESTORE="invenio-restore-drill-${DRILL_ID}"
velero restore create "$DRILL_RESTORE" --from-backup "$DRILL_BACKUP" --wait

# 4) Validate data + workload/dependency baseline
kubectl -n invenio get configmap invenio-restore-drill-canary -o jsonpath='{.data.drill-id}{"\n"}'
kubectl -n invenio get deploy invenio-web -o jsonpath='{.status.availableReplicas}{" / "}{.status.replicas}{"\n"}'
kubectl -n invenio get svc invenio-postgresql invenio-redis invenio-search
```

Invenio restore drill success criteria:
- Restore is `Completed` with no unexpected errors.
- `invenio-restore-drill-canary` is restored and `drill-id` matches.
- `invenio-web` reports expected deployment availability.
- ExternalName dependency Services (`invenio-postgresql`, `invenio-redis`, `invenio-search`) are present.

#### Backup staleness/failure observation checks

```bash
# Weekly schedule health (expected schedule: weekly-infra-backup)
kubectl -n velero get schedule weekly-infra-backup

# Latest scheduled backup outcomes
kubectl -n velero get backups \
  -l velero.io/schedule-name=weekly-infra-backup \
  --sort-by=.status.completionTimestamp \
  -o custom-columns=NAME:.metadata.name,PHASE:.status.phase,COMPLETED:.status.completionTimestamp,ERRORS:.status.errors,WARNINGS:.status.warnings

# Investigate a failed or stale backup
velero backup describe <backup-name> --details
velero backup logs <backup-name>
```

Operational note: for a weekly schedule, treat backups as stale if there is no recent `Completed` backup in roughly the last 8 days.

## License

MIT
