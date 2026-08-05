# Flux v2 Migration Design

**Goal:** Migrate a Kubernetes cluster from Flux v1 (single daemon, single Git repo) to Flux v2 (GitOps Toolkit controllers and CRDs) using a side-by-side coexistence strategy with explicit cutover and full rollback capability.

**Architecture:** Flux v1 and v2 are architecturally distinct and have no in-place upgrade path; this CR runs both simultaneously during a configurable coexistence window. The executor exports v1 state, bootstraps v2 in a separate namespace (`flux-system`), mirrors v1 GitRepository and Kustomization sources, verifies v2 reconciliation, then suspends v1 before removing it — with rollback possible at every phase boundary. HelmRelease migration is handled separately: the executor translates v1 HelmRelease CRs (different schema) into v2 equivalents.

## Phases

1. **Preflight** — Verify Flux v1 is running (`kubectl get deploy flux -n flux`); record v1 `--git-url`, `--git-branch`, `--git-path` args from the Flux Deployment spec; confirm v1 sync status via `fluxctl sync --k8s-fwd-ns flux`; verify Git repo is reachable from cluster; check for Helm Operator v1 (`kubectl get deploy helm-operator -n flux`) and v1 HelmRelease CRs (incompatible schema — must be translated); verify target cluster has sufficient capacity for four v2 controllers; confirm kubeconfig connector is valid and has cluster-admin rights.

2. **Snapshot** — `kubectl get all -n flux -o yaml > /tmp/flux-v1-backup.yaml`; `kubectl get helmrelease -A -o yaml > /tmp/helmreleases-v1-backup.yaml`; `fluxctl list-images --k8s-fwd-ns flux > /tmp/flux-v1-images.txt`; extract and serialize v1 Deployment args (git-url, git-branch, git-path, git-secret, etc.) to structured JSON for v2 config generation; store snapshot paths in CR execution context for rollback reference.

3. **Upgrade** — (1) Bootstrap Flux v2: `flux bootstrap <provider> --owner=<org> --repository=<repo> --branch=<branch> --path=<path>` — creates `flux-system` namespace and commits GitOps Toolkit manifests to the Git repo. (2) Create GitRepository CR mirroring v1 source: `flux create source git <name> --url=<url> --branch=<branch>`. (3) Create Kustomization CR targeting v1 git-path: `flux create kustomization <name> --source=<name> --path=<path> --prune=true --interval=1m`. (4) For each v1 HelmRelease: executor generates a v2 HelmRelease YAML from the v1 spec and applies it. (5) Wait `coexistence_period_seconds` with both stacks running; poll `flux get kustomizations` and `flux get helmreleases` until all show `Ready=True`. (6) Suspend v1: `kubectl annotate deploy/flux -n flux fluxcd.io/sync-state=paused`; hold for 5 minutes monitoring for drift. (7) Remove v1: `kubectl delete ns flux`; remove v1 CRDs.

4. **Verify** — `flux check` reports no errors; `flux get all -A` shows all sources and kustomizations Ready; `kubectl get pods -n flux-system` all Running; inject a test commit to the Git repo and confirm v2 reconciles the change within the configured interval; if `migrate_helm_releases=true`, confirm all HelmRelease objects show `Ready=True`.

5. **Rollback** — If v2 bootstrap failed: delete `flux-system` namespace — v1 was never touched. If v2 is running but not reconciling before v1 suspend: `kubectl annotate deploy/flux -n flux fluxcd.io/sync-state-` to resume v1, then delete `flux-system`. If v1 was already deleted: `kubectl apply -f /tmp/flux-v1-backup.yaml` restores the full v1 state including CRDs and Deployments. Git repo retains the bootstrap commit but it is inert without the `flux-system` namespace.

## CR Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `kubeconfig_connector_id` | string | yes | — | Connector ID providing cluster access |
| `git_url` | string | yes | — | Git repository URL to sync |
| `git_provider` | string | no | `"github"` | `"github"`, `"gitlab"`, or `"generic"` |
| `git_owner` | string | conditional | — | Required for github/gitlab providers |
| `git_repo_name` | string | conditional | — | Required for github/gitlab providers |
| `git_branch` | string | no | `"main"` | Branch to sync |
| `git_path` | string | no | `"./"` | Path within repo to reconcile |
| `v1_namespace` | string | no | `"flux"` | Namespace where Flux v1 is installed |
| `flux_v2_version` | string | no | latest stable | Flux v2 CLI/controller version to install |
| `migrate_helm_releases` | bool | no | `true` | Translate v1 HelmRelease CRs to v2 schema |
| `coexistence_period_seconds` | int | no | `300` | Time both stacks run together before v1 suspend |
| `dry_run` | bool | no | `false` | Plan only — no cluster mutations |

## Rollback Capability

**FULL.** v1 is suspended, not deleted, until the executor reaches the final removal step. At every prior phase boundary, v1 can be resumed with a single annotation removal. If v1 has been deleted, the executor restores it deterministically from the pre-migration snapshot YAML. The Git bootstrap commit remains in the repo but is harmless without the `flux-system` namespace — it does not interfere with v1 operation.

## Smoke Test Requirements

- kind cluster on EC2 (same VPC as platform) with Flux v1 installed and actively syncing a test Git repository
- Test repo must contain at least one Kustomization path and one v1 HelmRelease CR
- Smoke phases: migrate to v2 → verify all objects Ready → trigger test commit → confirm v2 reconciles → execute rollback → confirm v1 resumes syncing
- EC2 runner connects to cluster via kubeconfig stored in platform DB connector; no local Docker or mocked kubectl

## Key Risks

- **HelmRelease schema incompatibility** — v1 and v2 HelmRelease CRDs are structurally different; the executor must correctly translate `values`, `valuesFrom`, `rollback`, and `wait` fields or v2 HelmReleases will fail to reconcile. Mitigation: dry-run generates translated YAMLs for operator review before apply; untranslatable fields surface as plan-phase warnings.
- **Bootstrap commit pollutes Git repo** — `flux bootstrap` writes manifests directly to the target Git repo; if rollback is triggered after this step, the commit remains. Mitigation: documented in rollback output; operator is prompted to open a PR or revert the commit; the platform does not automatically rewrite Git history.
- **Coexistence drift window** — while both stacks run, a resource managed by v1 and mirrored by v2 could be reconciled by both, causing a brief fight. Mitigation: Kustomization `prune=true` is not enabled on v2 until v1 is suspended; executor validates no overlapping managed fields before cutover.
