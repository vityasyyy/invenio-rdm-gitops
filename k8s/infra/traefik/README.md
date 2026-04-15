# Traefik Ingress Controller

Traefik v3.6.11 — the ingress layer that sits between Cloudflare Tunnel and internal services.

## Management

Traefik is managed by **ArgoCD** using the Helm chart. The `values.yaml` file in this directory is the source of truth.

ArgoCD Application: `argocd/apps/traefik.yaml`

### How It Works

```
GitHub (values.yaml)
    ↓ (ArgoCD watches for changes)
ArgoCD Application (traefik)
    ↓ (Helm deployment)
Traefik Controller (traefik namespace)
```

**To update Traefik**: Edit `values.yaml` and commit to Git. ArgoCD will automatically sync the changes.

### Current Configuration

| Setting | Value |
|---------|-------|
| Chart | traefik v39.0.6 |
| App Version | v3.6.11 |
| Image | docker.io/traefik:v3.6.11 |
| Replicas | 1 |
| Service Type | ClusterIP |
| Watched Namespaces (CRD) | traefik, argocd, monitoring |
| Watched Namespaces (Ingress) | traefik, argocd, monitoring |
| Default IngressClass | traefik |

## Architecture

```
Cloudflare Tunnel → traefik:8000 (web entryPoint)
                           │
                           ├─ IngressRoute: argocd.vityasy.me → argocd-server:8080
                           ├─ IngressRoute: *.vityasy.me → (future services)
                           └─ IngressRoute: grafana.vityasy.me → grafana (monitoring)
```

## Key Configuration Notes

- **`providers.kubernetesCRD.namespaces`**: Controls which namespaces Traefik scans for IngressRoute resources. Add new namespaces here when deploying new services with IngressRoutes.
- **`providers.kubernetesIngress.namespaces`**: Controls which namespaces Traefik scans for standard Kubernetes Ingress resources.
- **`service.type: ClusterIP`**: Uses internal DNS. Cloudflare Tunnel connects directly to the internal service.

## Troubleshooting

```bash
# Check Traefik logs
kubectl logs -n traefik -l app.kubernetes.io/name=traefik --tail=50

# Verify IngressRoutes are discovered
kubectl get ingressroute -A

# Check Traefik dashboard (port-forward)
kubectl port-forward -n traefik deploy/traefik 9000:9000
# Open http://localhost:9000/dashboard/

# Force ArgoCD sync
argocd app sync traefik
```
