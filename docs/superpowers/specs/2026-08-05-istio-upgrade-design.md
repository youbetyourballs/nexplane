# Istio Rolling Upgrade Design

**Goal:** Upgrade Istio service mesh to a target version using the canary (revision-based) pattern to achieve zero-downtime data plane migration across all labeled namespaces.

**Architecture:** A new Istio control plane revision is installed alongside the existing one, allowing the data plane to be migrated namespace by namespace by switching revision labels and restarting workloads. The old control plane remains live until all namespaces are confirmed healthy on the new revision, making the upgrade fully reversible up to the final removal step. Executor is Python async and integrates with the Nexplane Kubernetes connector for all cluster operations.

## Phases

1. **Preflight** — Run `istioctl version` to record current control plane and data plane versions. Run `istioctl proxy-status` to verify all proxies are within one minor version of the control plane (skew check). Run `istioctl analyze --all-namespaces` and assert zero errors. Enumerate CRDs via `kubectl get crd | grep istio.io` and record versions. Detect install method (istioctl or Helm) and confirm cluster has sufficient resources to host a second control plane instance.

2. **Snapshot** — Export the live IstioOperator manifest: `kubectl get istiooperator -A -o yaml > /tmp/istio-backup.yaml`. Export all Istio CRs (VirtualService, DestinationRule, Gateway, PeerAuthentication, AuthorizationPolicy, RequestAuthentication) for every namespace to `/tmp/istio-crs-backup.yaml`. Record current revision label values on all namespaces with `istio-injection=enabled` or an existing `istio.io/rev` label.

3. **Upgrade** — Install new control plane revision: `istioctl install --set revision=<new-revision> -y`. Verify new control plane pods are healthy in `istio-system`. For each namespace batch (size controlled by `namespace_batch_size`): update the namespace label to `istio.io/rev=<new-revision>`, run `kubectl rollout restart deploy -n <namespace>`, and wait up to `rollout_restart_wait_seconds` for all pods to reflect the new sidecar version via `istioctl proxy-status`. After all namespaces are migrated: remove the old control plane revision with `istioctl uninstall --revision <old-revision> -y` and clean up legacy `istio-injection=enabled` labels.

4. **Verify** — Run `istioctl proxy-status` and assert all proxies report the new version. Run `istioctl analyze --all-namespaces` and assert zero errors. Confirm only new-revision pods are running in `istio-system`. Optionally send a curl request through the ingress gateway to validate live traffic routing.

5. **Rollback** — If the new control plane was never installed: no action required. If data plane migration is in progress: switch each affected namespace label back to the old revision and restart deployments. If the old control plane was already removed: reinstall it with `istioctl install --set revision=<old-revision>` and restore CRDs from snapshot. Saved CR manifests are applied via `kubectl apply -f /tmp/istio-crs-backup.yaml`.

## CR Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `target_version` | string | yes | — | Target Istio version, e.g. "1.20.3" |
| `current_revision` | string | no | auto-detected | Existing control plane revision label |
| `new_revision` | string | no | derived from version | New revision label, e.g. "1-20" |
| `kubeconfig_connector_id` | string | yes | — | Nexplane Kubernetes connector ID |
| `namespaces` | list[string] | no | all labeled | Namespaces to migrate; omit for all |
| `namespace_batch_size` | int | no | 1 | Namespaces to migrate simultaneously |
| `rollout_restart_wait_seconds` | int | no | 120 | Per-namespace rollout wait timeout |
| `install_method` | string | no | "istioctl" | "istioctl" or "helm" |
| `helm_chart_repo` | string | no | — | Helm repo URL when install_method=helm |
| `dry_run` | bool | no | false | Plan without executing any cluster changes |

## Rollback Capability

**FULL** — The canary pattern keeps the old control plane alive throughout data plane migration, so any namespace can be reverted by switching its label back. The only semi-irreversible step is removing the old control plane revision at the end; this is mitigated by retaining the IstioOperator and CR snapshots, allowing reinstallation of the old revision. Old CRs remain syntactically compatible across minor Istio versions.

## Smoke Test Requirements

A Kubernetes cluster running on EC2 (kind or k3s) with Istio 1.19 installed via istioctl, one test namespace with a sample deployment injected, and a configured Nexplane Kubernetes connector pointing at the cluster. The smoke run upgrades to Istio 1.20, migrates the test namespace, asserts `istioctl proxy-status` shows version 1.20 on all proxies, then executes rollback to confirm the namespace label and proxy version revert cleanly.

## Key Risks

- **Proxy version skew during migration:** If a workload pod fails to restart, it stays on the old sidecar version while the namespace label has advanced. The executor monitors `istioctl proxy-status` after each batch and aborts with a rollback signal before proceeding to the next namespace if skew is detected.
- **Insufficient cluster resources for dual control planes:** Running two Istio control planes simultaneously requires roughly double the istiod memory and CPU. The preflight phase checks available node capacity and blocks the upgrade if headroom is below a safe threshold.
- **Old control plane removal is hard to undo quickly:** Once `istioctl uninstall --revision <old-revision>` runs, the old control plane is gone and reinstallation takes minutes. The executor only reaches this step after all namespaces are verified on the new revision, and `dry_run` mode can be used to rehearse the full sequence before committing.
