# Infrastructure Setup Guide

GitOps infrastructure setup for Kubernetes - ArgoCD, Sealed Secrets, and Cloudflare Tunnel.

## Prerequisites

- `kubectl` configured with cluster access
- `kubeseal` CLI installed: `brew install kubeseal` (macOS) or `go install github.com/bitnami-labs/sealed-secrets/cmd/kubeseal@latest`
- `terraform` installed (for tunnel management): `brew install terraform`

---

## Architecture

```
Internet
  └─► Cloudflare Edge (SSL termination, DDoS protection)
        └─► Cloudflare Tunnel → Traefik (traefik namespace)
              └─► ArgoCD UI at argocd.vityasy.me

GitHub (this repo)
  └─► ArgoCD (argocd namespace)
        └─► Syncs manifests from Git
              └─► Wave -3: Sealed Secrets controller

Sealed Secrets Controller (kube-system)
  └─► Decrypts SealedSecret CRDs → creates Kubernetes Secrets
```

---

## Step-by-Step Deployment

### Phase 0: Pre-Flight Check

```bash
# Verify cluster access
kubectl cluster-info
kubectl get nodes

# Verify storage class exists
kubectl get storageclass btd-nfs

# Verify Traefik is running (Rancher pre-installed)
kubectl get pods -n traefik

# Verify cloudflared is available (optional, for manual tunnel testing)
cloudflared --version
```

---

### Phase 1: Bootstrap Infrastructure

```bash
# 1. Install ArgoCD only (everything else is ArgoCD-managed)
./scripts/bootstrap-infra.sh

# This will:
#   - Install ArgoCD via kustomize (k8s/infra/argocd/)
#   - Apply the infra project to ArgoCD
#   - Display ArgoCD admin password
#
# After this, ArgoCD automatically syncs:
#   Wave -5: security-policies (NetworkPolicies, LimitRanges, ResourceQuotas)
#   Wave -4: traefik (Helm), cloudflared (DaemonSet)
#   Wave -3: sealed-secrets controller, argocd-self
#   Wave -2: cert-manager
#   Wave  2: velero
#   Wave  5: monitoring (Prometheus/Grafana)

# 2. Activate Cloudflare Tunnel
./external-lb/scripts/bootstrap-dev.sh

# This will:
#   - Run Terraform to ensure tunnel + DNS exists in Cloudflare
#   - Create cloudflared-credentials secret (tunnel token)
#   - ArgoCD deploys the cloudflared DaemonSet automatically

# 3. Verify infra is healthy
./scripts/verify-infra.sh
```

---

### Phase 2: Verify Infrastructure

```bash
# Run comprehensive infrastructure checks
./scripts/verify-infra.sh

# This verifies:
#   - ArgoCD is running
#   - Security policies are applied (NetworkPolicies, LimitRanges, ResourceQuotas)
#   - Traefik is running
#   - Sealed Secrets controller is running
#   - cloudflared DaemonSet is running
#   - ArgoCD UI is accessible via tunnel
#   - StorageClass is correct
```

---

### Phase 3: Secure ArgoCD

```bash
# 1. Log into ArgoCD
#    URL: https://argocd.vityasy.me
#    Username: admin
#    Password: (from bootstrap-infra.sh output)

# 2. Change the default password
argocd account update-password

# 3. Verify all infra apps are synced in ArgoCD UI
#    All apps should be green/Healthy in the 'infra' project
```

---

## ArgoCD Applications

| App | Wave | What it deploys |
|------|-------|-----------------|
| `security-policies` | -5 | NetworkPolicies, LimitRanges, ResourceQuotas, PodSecurityAdmission |
| `traefik` | -4 | Traefik ingress controller (Helm chart) |
| `cloudflared` | -4 | Cloudflare Tunnel DaemonSet (kube-system) |
| `sealed-secrets` | -3 | Sealed Secrets controller (kube-system) |
| `argocd-self` | -3 | ArgoCD self-management (kustomize manifests) |
| `cert-manager` | -2 | Cert-manager for TLS (Helm chart) |
| `velero` | 2 | Velero for cluster backups (Helm chart) |
| `monitoring` | 5 | Prometheus/Grafana stack (Helm chart) |
| `invenio-bootstrap` | 7 | Invenio bootstrap stack (config scaffold, external deps wiring, sealed secret template, ingress routes) |

**Note:** ArgoCD itself is initially installed via `bootstrap-infra.sh`, then manages its own lifecycle via `argocd-self` (wave -3). After bootstrap, changes to `k8s/infra/argocd/` are synced by ArgoCD automatically.

---

## Secrets Management

### How Sealed Secrets Work

```
1. ArgoCD deploys Sealed Secrets controller (wave -3)
2. Controller generates RSA key pair (private key stays in cluster)
3. Public key fetched from cluster for sealing secrets:
     kubeseal --fetch-cert > secrets/sealed-secrets-public.pem
4. YOU BACK UP THE PRIVATE KEY to password manager (CRITICAL!)
   Export: kubectl get secret -n kube-system sealed-secrets-key* \
     -o jsonpath='{.data.tls\.key}' | base64 -d > secrets/sealed-secrets-private.pem
5. When you deploy an app, you seal secrets:
      kubectl create secret generic my-secret --dry-run=client -o yaml |
        kubeseal --cert secrets/sealed-secrets-public.pem > sealed-secret.yaml
6. SealedSecret (encrypted YAML) is SAFE to commit to Git
7. ArgoCD syncs SealedSecret to cluster
8. Controller decrypts it into a regular Kubernetes Secret
9. Your app pods use the secret via volume mounts or env vars
```

### Disaster Recovery: Restoring the Private Key

If the cluster is destroyed and you need to recreate SealedSecrets:

```bash
# 1. Restore the private key from your password manager
# 2. Recreate the sealed-secrets-key secret in kube-system
kubectl create secret tls sealed-secrets-key \
  -n kube-system \
  --cert=sealed-secrets-public.pem \
  --key=sealed-secrets-private.pem

# 3. The Sealed Secrets controller will pick it up
# 4. Existing SealedSecret CRDs in Git will decrypt correctly
```

### Generating New Sealed Secrets

Use the centralized generator instead of hand-writing `kubectl create secret | kubeseal` pipelines:

```bash
# Rotate everything managed by the generator
./scripts/generate-sealed-secrets.sh

# Rotate only Invenio
./scripts/generate-sealed-secrets.sh invenio

# Override one value and reseal only Invenio
INVENIO_SECRET_KEY="$(openssl rand -hex 32)" \
  ./scripts/generate-sealed-secrets.sh invenio

# Seal Cloudflare tunnel token if you have a new token from Cloudflare
CLOUDFLARE_TUNNEL_TOKEN="$TOKEN" ./scripts/generate-sealed-secrets.sh cloudflared
```

### Secrets policy

- `k8s/apps/invenio/app-sealed-secret.yaml` contains ciphertext only under `spec.encryptedData`; the plaintext is generated locally when the script runs and stays under the gitignored `secrets/` directory.
- Current Invenio sealed keys are `SQLALCHEMY_DATABASE_URI`, `CACHE_REDIS_URL`, `OPENSEARCH_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, and `INVENIO_SECRET_KEY`.
- MinIO is a shared instance for this cluster, so the generator reuses the current MinIO credential pair for Velero and Invenio by default; override `INVENIO_S3_ACCESS_KEY_ID` / `INVENIO_S3_SECRET_ACCESS_KEY` if you add a narrower MinIO user later.
- ArgoCD's Redis is control-plane storage only and must not be reused by Invenio.
- Rotation workflow: update the relevant env vars, rerun `./scripts/generate-sealed-secrets.sh <component>`, review the sealed YAML diff, commit only the sealed file(s), then let ArgoCD sync the change.
- Invenio dependency wiring currently uses `ExternalName` services; Redis resolves to `redis-master.redis.svc.cluster.local` via `k8s/apps/invenio/dependencies-external-services.yaml`.

---

## Cloudflare Tunnel

### What It Does

- Creates an **outbound-only** connection from cluster to Cloudflare
- Routes `*.vityasy.me` to your Traefik ingress
- Provides **zero inbound firewall holes** - no need to open ports
- Includes **DDoS protection** via Cloudflare's global network
- Manages **automatic SSL** certificates at the edge

### Files

```
external-lb/terraform/
├── envs/dev/
│   ├── main.tf        # Instantiates tunnel module
│   ├── providers.tf   # Cloudflare provider config
│   ├── variables.tf  # Input variables
│   ├── .env          # Your Cloudflare credentials (gitignored)
│   └── .env.example  # Template for .env
└── modules/cloudflare-tunnel/
    ├── main.tf       # Creates tunnel, DNS, config
    ├── variables.tf  # Module inputs
    └── outputs.tf    # Module outputs
```

### Variables Required

In `external-lb/terraform/envs/dev/.env`:

| Variable | Description | Where to find |
|----------|-------------|----------------|
| `TF_VAR_cloudflare_api_token` | API token | Cloudflare Dashboard → My Profile → API Tokens |
| `TF_VAR_account_id` | Cloudflare account ID | Cloudflare Dashboard → Workers & Pages (in URL) |
| `TF_VAR_zone_id` | Domain zone ID | Cloudflare Dashboard → Your Domain → Overview (API section) |
| `TF_VAR_tunnel_secret` | 32-byte base64 random secret | Generate: `openssl rand -base64 32` |

### Updating Tunnel Configuration

To change the hostname or service:

```bash
# 1. Edit external-lb/terraform/envs/dev/main.tf
vim external-lb/terraform/envs/dev/main.tf

# 2. Apply changes
cd external-lb/terraform/envs/dev
terraform apply

# 3. The cloudflared DaemonSet will automatically pick up new config
```

---

## Troubleshooting

### ArgoCD won't start

```bash
# Check ArgoCD pods
kubectl get pods -n argocd

# Check ArgoCD logs
kubectl logs -n argocd -l app.kubernetes.io/name=argocd-server -f

# Verify Traefik is accessible
kubectl get svc -n traefik
```

### Sealed Secret won't unseal

```bash
# Check controller is running
kubectl get pods -n kube-system -l app.kubernetes.io/name=sealed-secrets-controller

# Check controller logs
kubectl logs -n kube-system -l app.kubernetes.io/name=sealed-secrets-controller -f

# Verify public key matches cluster
kubeseal --fetch-cert \
  https://kubernetes.default.svc \
  > /tmp/cert.pem
diff secrets/sealed-secrets-public.pem /tmp/cert.pem
```

### Cloudflare Tunnel not connecting

```bash
# Check cloudflared pods
kubectl get pods -n kube-system -l app=cloudflared

# Check logs
kubectl logs -n kube-system -l app=cloudflared --tail=50

# Verify tunnel is active in Cloudflare dashboard
# Cloudflare Dashboard → Zero Trust → Networks → Tunnels
```

### ArgoCD app is stuck in OutOfSync

```bash
# List all apps and their status
argocd app list

# Sync manually
argocd app sync sealed-secrets

# Watch the sync
argocd app get sealed-secrets --watch
```

### Velero backup is stale/failed or restore drill is needed

```bash
# Verify schedule and storage location are healthy
kubectl -n velero get schedule weekly-infra-backup
kubectl -n velero get backupstoragelocation default -o jsonpath='{.status.phase}{"\n"}'

# Check latest scheduled backup results
kubectl -n velero get backups \
  -l velero.io/schedule-name=weekly-infra-backup \
  --sort-by=.status.completionTimestamp \
  -o custom-columns=NAME:.metadata.name,PHASE:.status.phase,COMPLETED:.status.completionTimestamp,ERRORS:.status.errors,WARNINGS:.status.warnings

# Investigate a failed backup
velero backup describe <backup-name> --details
velero backup logs <backup-name>
```

For this weekly schedule, alert if no `Completed` backup exists in ~8 days.
Use the README Velero runbook for manual backup trigger and canary restore drill.

---

## Security Checklist

- [ ] Sealed Secrets private key backed up to password manager
- [ ] `secrets/` directory is in .gitignore (never commit secrets)
- [ ] Terraform state files are gitignored
- [ ] ArgoCD admin password changed from default
- [ ] Cloudflare API token has minimal permissions (Zone:Edit, Account:Edit)
- [ ] ArgoCD is not publicly accessible except via tunnel

---

## Invenio RDM Prerequisite Dependency Design

This section defines the concrete dependency architecture for deploying InvenioRDM into the existing cluster foundation.

### 1) Dependency mapping to current infrastructure

| Invenio dependency | Required for | Cluster mapping | Current state |
|---|---|---|---|
| PostgreSQL | Metadata DB, accounts, records | Deploy new Postgres service reachable from `invenio` namespace (`5432/TCP`) | **Not deployed yet** in this repo |
| Redis | Cache + Celery broker/result backend | Deploy new Redis service reachable from `invenio` namespace (`6379/TCP`) | **Not deployed yet** in this repo |
| OpenSearch/Elasticsearch | Search index backend | Deploy new search service reachable from `invenio` namespace (`9200/TCP`) | **Not deployed yet** in this repo |
| S3 object storage buckets | File/object payloads | Use existing in-cluster MinIO (`minio.minio.svc.cluster.local:9000`) | **Available**; Invenio-specific buckets still need creation |
| Ingress + TLS | UI/API exposure | Existing Cloudflare Tunnel → Traefik ingress (`traefik` namespace); cert-manager for in-cluster TLS if needed | **Available** |
| Secrets | Credentials/config injection | Existing SealedSecrets controller (`kube-system`) with Git-safe encrypted secrets | **Available** |
| Monitoring | Metrics/alerts | Existing Prometheus/Grafana stack (`monitoring` namespace) | **Available** |
| Backup/DR | Namespace backup and object storage backup target | Existing Velero setup already includes `invenio` namespace in schedule | **Available** |

### 2) Object storage plan (MinIO)

Use MinIO as the only S3 endpoint for InvenioRDM:

- Endpoint: `http://minio.minio.svc.cluster.local:9000`
- Credentials source: SealedSecret in `invenio` namespace (do not reuse plain secrets)
- Required buckets (minimum):
  - `invenio-rdm-files` (primary records/files bucket)
  - `invenio-rdm-uploads` (multipart/staging uploads)

Implementation note: the existing MinIO post-sync job only creates `velero-backups`; add equivalent bucket bootstrap for Invenio buckets before Invenio app rollout.

### 3) `invenio` namespace resource and storage strategy

The namespace already exists with governance (`k8s/infra/namespaces/invenio.yaml`):

- ResourceQuota: `requests.cpu=4`, `requests.memory=8Gi`, `limits.cpu=8`, `limits.memory=16Gi`, `persistentvolumeclaims=20`
- LimitRange defaults: request `100m/128Mi`, limit `500m/512Mi`, max `2 CPU / 4Gi`

Deployment strategy in this namespace:

1. Keep Invenio web/worker pods in `invenio` namespace.
2. Prefer externalized stateful dependencies (Postgres/Redis/Search) in dedicated namespaces or managed services; consume over ClusterIP/service DNS.
3. Use PVCs in `invenio` only for app-local needs (if any), with `btd-nfs` and `Retain` reclaim policy expectations.
4. Keep object payloads in MinIO buckets (not on app PVCs) so Velero + object storage cover DR.

### 4) Network policy implications (critical for bootstrap)

Current `invenio` policies enforce default deny ingress+egress and only allow:

- ingress from `traefik` namespace
- egress to DNS (`53/TCP+UDP`)
- egress to internet HTTPS (`443/TCP`)

Before Invenio starts, add explicit egress allows from `invenio` to:

- Postgres service (`5432/TCP`)
- Redis service (`6379/TCP`)
- Search service (`9200/TCP`)
- MinIO service in `minio` namespace (`9000/TCP`)

Without these rules, app pods will fail dependency readiness checks despite services being healthy.

### 5) Rollout dependency order (for GitOps bootstrap todo)

Use this strict order:

1. **Foundation already synced**: security policies, Traefik/cloudflared, SealedSecrets, cert-manager, Velero, monitoring.
2. **Data plane dependencies**: Postgres, Redis, Search (plus PVC provisioning/health checks).
3. **Object storage prep**: create Invenio MinIO buckets and validate access with sealed credentials.
4. **Network policy expansion**: add `invenio` egress allows to DB/Redis/Search/MinIO.
5. **Invenio secrets/config**: apply SealedSecrets for DB URI, Redis URI, search endpoint, S3 creds, app secret keys.
6. **Invenio application manifests**: web, workers, jobs/migrations with startup probes tied to dependency readiness.
7. **Ingress routes**: add Traefik routes for Invenio hostnames and verify Cloudflare tunnel path.
8. **Post-deploy validation**: smoke test UI/API, worker queue processing, indexing, file upload/download, and metrics visibility.

---

## Next Steps

After infrastructure is verified:

1. **Export Sealed Secrets public key** (for sealing app secrets):
   ```bash
   kubectl get secret -n kube-system -l sealedsecrets.bitnami.com/sealed-secrets-key=active \
     -o jsonpath='{.data.tls\.crt}' | base64 -d > secrets/sealed-secrets-public.pem
   kubectl get secret -n kube-system -l sealedsecrets.bitnami.com/sealed-secrets-key=active \
     -o jsonpath='{.data.tls\.key}' | base64 -d > secrets/sealed-secrets-private.pem
   # BACK UP THE PRIVATE KEY!
   ```

2. **Deploy your application**: Create an ArgoCD Application in `argocd/apps/` for your workload
3. **Configure Velero credentials**: Create a SealedSecret for Velero's cloud credentials
4. **Set up Grafana admin password**: Create a SealedSecret for Grafana admin password
5. **Configure backups**: Velero is deployed but needs cloud credentials for S3/NFS backup target

For application deployment examples using this infrastructure, see the main repository documentation.
