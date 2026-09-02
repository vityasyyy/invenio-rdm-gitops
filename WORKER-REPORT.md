# WORKER-REPORT — Issue #67: Wire SMTP mail config with placeholders (T2)

Branch: `feat/67-mail-config` · Worktree: `.worktrees/feat/67-mail-config` · Date: 2026-09-02

## What changed

| File | Change |
|---|---|
| `docker/ugm/invenio.cfg` | New Mail section (K8s overrides block): 8 settings read from env with localhost defaults — `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USE_TLS`, `MAIL_USE_SSL`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_DEFAULT_SENDER` (defaults to `INSTANCE_ADMIN_EMAIL`), `SECURITY_EMAIL_SENDER` (defaults to `MAIL_DEFAULT_SENDER`) |
| `k8s/apps/invenio/app-config.yaml` | Non-secret mail vars: `MAIL_SERVER: smtp.example.org` (placeholder), `MAIL_PORT: "587"`, `MAIL_USE_TLS: "true"`, `MAIL_DEFAULT_SENDER: noreply@invenio.vityasy.me` + comment pointing at the SealedSecret for creds |
| `k8s/apps/invenio/app-sealed-secret.yaml` | `MAIL_USERNAME`/`MAIL_PASSWORD` added to `invenio-app-secrets` — **sealed with kubeseal** (offline, using the local controller public key `~/.sealed-secrets/sealed-secrets-public.pem`), placeholder values, strict scope (no `spec.scope` field, matching existing entries) |
| `docs/plans/active/2026-09-02-email-confirmation.md` | New plan doc: why, goals, mermaid architecture, files table, task groups, acceptance criteria, **Operator steps** (kubeseal recipe), verification steps |
| `docs/cluster-assessment-2026-09-02.md` | New assessment: 3 nodes Ready, 16/17 ArgoCD apps Synced+Healthy (invenio-bootstrap Progressing), findings (a)–(g), Concerns requiring follow-up |
| `docs/plans/README.md` | New plan added to Active table |

## Verification output

- `grep MAIL_ docker/ugm/invenio.cfg` → all 8 settings present (lines 291–298)
- `kustomize build k8s/apps/invenio` → **RENDER OK** (ConfigMap + SealedSecret both carry MAIL_ entries)
- `scripts/ci-render-manifests.sh` → **All renders succeeded** (19 manifests)
- `yamllint k8s/apps/invenio/app-config.yaml k8s/apps/invenio/app-sealed-secret.yaml` → **passes**
- Plaintext scan: no `smtp-placeholder`/`placeholder-change-me`/private-key material anywhere in the diff — placeholder values exist only inside the sealed ciphertext
- Seal validity: public cert and private key SHA256 fingerprints match (`9624d5ce…`), i.e. the seal was made with the correct controller key pair

## kubeseal recipe for the operator (when university relay creds arrive)

Existing SealedSecret entries have **no `spec.scope`** → kubeseal default **strict** scope (name `invenio-app-secrets` + namespace `invenio` must match). Re-seal with the same scope:

```bash
# Online (controller reachable):
kubectl create secret generic invenio-app-secrets \
  --namespace invenio \
  --from-literal=MAIL_USERNAME='<relay-username>' \
  --from-literal=MAIL_PASSWORD='<relay-password>' \
  --dry-run=client -o yaml |
  kubeseal --controller-namespace kube-system \
    --controller-name sealed-secrets-controller \
    --format yaml --namespace invenio --name invenio-app-secrets

# Offline (no cluster access; key cached at ~/.sealed-secrets/):
kubectl create secret generic invenio-app-secrets \
  --namespace invenio \
  --from-literal=MAIL_USERNAME='<relay-username>' \
  --from-literal=MAIL_PASSWORD='<relay-password>' \
  --dry-run=client -o yaml |
  kubeseal --cert ~/.sealed-secrets/sealed-secrets-public.pem \
    --format yaml --namespace invenio --name invenio-app-secrets
```

Then: copy the two `MAIL_*` lines into `k8s/apps/invenio/app-sealed-secret.yaml`, replace `MAIL_SERVER: "smtp.example.org"` in `app-config.yaml` with the real relay host, commit, push (ArgoCD auto-syncs; no image rebuild needed — `k8s/` changes don't trigger `build-image.yaml`). Verify the seal decrypts: `kubectl get secret invenio-app-secrets -n invenio -o jsonpath='{.data.MAIL_USERNAME}' | base64 -d`.

## What remains for the lead

1. Review this branch, push, open PR (`feat: wire SMTP mail config with placeholders (#67)`), wait for CI (`validate-infra.yaml` — yamllint + kustomize render + kubeconform + gitleaks), squash-merge.
2. After merge: ArgoCD syncs ConfigMap + SealedSecret; pods pick up env on restart. No image rebuild expected (docker/ugm change WILL trigger `build-image.yaml` — the invenio.cfg edit is under `docker/ugm/**`, so a new image build is expected and required for the MAIL_* env wiring to reach the app).
3. Track the operator step: real SMTP relay credentials → re-seal + replace placeholder host (plan doc "Operator steps").
4. Follow-ups from the assessment: DB HA (postgres-1/2 crash-looping 18d), worker-02 memory overcommit (88% requests / 360% limits), `:latest` image drift, kubelet-proxy 502s on worker-02, stale `keys.txt`.

## Escalation notes

- kubeseal WAS available (`/opt/homebrew/bin/kubeseal`, v0.36.6) and sealing succeeded offline with the cached controller public key — no template fallback needed.
- Cluster API was unreachable (no VPN) — all sealing done offline with the local key; key-pair fingerprint verified against the private key.
