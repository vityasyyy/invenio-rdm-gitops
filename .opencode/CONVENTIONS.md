# InvenioRDM GitOps Project Conventions (lead agent + workers)

Project-specific conventions for the InvenioRDM GitOps repo. Read at session
start. General engineering behavior lives in `.opencode/skills/AGENTS.md` and
the global lead agent config (skill trigger map).

## Repo layout & GitOps

- GitOps-first: everything is YAML manifests or Helm values committed to this
  repo and synced by ArgoCD (app-of-apps: `argocd/root.yaml` + `argocd/apps/`).
  `k8s/apps/` = application workloads (invenio, invenio-deps/{postgresql,
  redis, opensearch}), `k8s/infra/` = platform (argocd, traefik, monitoring,
  loki, minio, velero, sealed-secrets, security), `external-lb/k8s/` =
  Cloudflare Tunnel + cloudflared daemonset, `docker/ugm/` = the vendored
  UGM Invenio instance layer (Dockerfile, invenio.cfg, uwsgi configs).
- Cluster: `btd-rke2` (Rancher-managed RKE2, reachable only via university
  VPN). Storage: NFS CSI (`btd-nfs` StorageClass, `nfs.csi.k8s.io`). Domains:
  `invenio.vityasy.me` (UI) and `api-invenio.vityasy.me` (API) via Cloudflare
  Tunnel → Traefik IngressRoutes.
- Image: `ghcr.io/vityasyyy/invenio-ugm` — built and pushed from THIS repo by
  `.github/workflows/build-image.yaml` (triggers on `docker/ugm/**`,
  `site/**`, `templates/**`, `static/**`, `translations/**`, `app_data/**`,
  `assets/**`). Never push from the upstream `gunturbudi` repo. Pin digests in
  kustomization after image-updater updates.
- Secrets: SealedSecrets (encrypted in git, `bitnami.com/v1alpha1`), sealed
  via `kubeseal`; the private key lives OUTSIDE the repo (`~/.sealed-secrets/`).
  Never commit plaintext secrets; `secrets/` and `keys.txt` are gitignored.
- ExternalName services: `k8s/apps/invenio/dependencies-external-services.yaml`
  maps invenio-deps services to their real namespaces — keep in sync when
  moving dependencies.

## Git & worktrees

- **Worktrees live in `.worktrees/`** (e.g. `.worktrees/feat/65-project-conventions`),
  never in /tmp or the temp dir. `.worktrees/` is gitignored. Worker branches
  are cut from `origin/main` AFTER `git fetch`; never from a stale base
  (`git rev-list --count origin/main..HEAD` must be 0 before pushing a PR).
- **Branch-from-issue convention** (issue-to-pr-workflow skill): every change
  starts with a GitHub issue, tiered T1/T2/T3. Branch name format
  `<prefix>/<issue-number>-<short-slug>` — `fix/42-probe-timeouts`,
  `feat/55-invenio-v12-upgrade`. T1 → `fix/`, T2/T3 → `feat/`.
- **PRs only, squash-merge.** Worker branches are never merged directly into
  main. The lead rebuilds the wave as a clean feature branch from
  `origin/main` with Conventional Commits, opens a PR with a full body
  (Summary/Why/Implementation/Testing/Risks/Rollback), waits for CI
  (`gh pr checks --watch`), then squash-merges (`gh pr merge <n> --squash
  --delete-branch`). No direct-to-main commits.
- Conventional Commits: `type(scope): description` + body with why/what.
- If a PR's `gh pr view <n> --json files` shows files you did not intend to
  change, the base is stale — close, re-cut, re-push.

## Workers (herd)

- Workers are spawned via `herd spawn <name> <worktree-path>` in herdr panes,
  each in its own worktree + branch (parallel-worktrees skill). Workers never
  share a working directory.
- Overlap check before dispatch: two workers must not touch the same file.
- Worker briefs are self-contained (goal, scope, do-not-touch list,
  acceptance criteria, WORKER-REPORT.md output contract, escalation rule).
- Escalation: route every worker `blocked` state to the user first; never
  resolve approvals autonomously. Secrets are typed by the user via
  `herd attach` directly in the worker pane — the lead never sees or types
  them. Secrets go in SealedSecrets (encrypted in git) / GitHub secrets —
  never typed by the lead into a brief.

## Docs (AGENTS.md rule 11 — docs follow code)

- Every PR that changes behavior updates the affected docs in the same PR:
  `docs/plans/README.md` (plans index), the relevant plan in
  `docs/plans/active/` (move completed plans to `completed/`, keep statuses
  accurate), and the README when architecture changes. A PR whose docs are
  stale is not done.
- Planning-first: any concern/finding/open question discovered during a wave
  goes into `docs/plans/active/` as part of the wave's PR.
- Wave finalization (wave-finalization skill): after all wave PRs merge,
  close worker panes, remove worker worktrees + branches, sync plans/docs
  (move completed plans, update indexes), verify the repo is clean.

## CI quirks (learned the hard way)

- ArgoCD auto-syncs from `main` (selfHeal + prune). A merged PR is not done
  until the cluster converges — verify via `deploy-verify.yaml` (ArgoCD REST
  API sync check + smoke test) or `kubectl` when on VPN.
- `build-image.yaml` only triggers on `docker/ugm/**` (and site/templates/
  static/translations/app_data/assets + the workflow itself). Changes to
  `k8s/` alone do NOT rebuild the image — don't expect a new image from a
  manifest-only PR.
- SealedSecrets: re-seal with `kubeseal` when a secret changes; a stale seal
  (e.g. dead GHCR PAT) silently blocks pulls — verify the sealed value
  decrypts before declaring a deploy green.
- No test-percentage gate in CI — CI is yamllint + kustomize render +
  kubeconform + selector validation + gitleaks (`validate-infra.yaml`).
- Image-updater can report `IMAGES: 0` and skip writing a digest — pin the
  digest in kustomization when the live image must not drift.
