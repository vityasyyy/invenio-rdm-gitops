# Infrastructure GitOps for Kubernetes

GitOps infrastructure repository for deploying and managing ArgoCD, Sealed Secrets, and Cloudflare Tunnel on a Rancher-managed Kubernetes cluster.

## Scope

This repository contains **infrastructure-only** resources. Applications are deployed separately using this foundation.

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

## License

MIT
