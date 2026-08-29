# Task 3 Report — K8s Node Pool Upgrade + Rollback Commands

## Status: COMPLETE

## Files Changed
- Created: `agent/commands/k8s/node_pool.go`
- Modified: `agent/commands/k8s/k8s_test.go` (added 2 validation tests)

## Tests
All 7 tests pass (0.679s):
- TestExtractMinor
- TestValidateVersionSkew
- TestDetectClusterType
- TestControlPlaneUpgradeValidation
- TestNodePoolUpgradeValidation (new)
- TestNodePoolRollbackValidation (new)
- TestBuildNodePoolsKubeadm

## Functions Exported
- `NodePoolUpgradeExecute(params map[string]any) (map[string]any, error)` — supports kubeadm, eks, gke, aks
- `NodePoolRollbackExecute(params map[string]any) (map[string]any, error)` — supports kubeadm (uncordon), eks, gke, aks

## Fix Summary (2026-08-29)

Two bugs were patched in `NodePoolRollbackExecute`: (1) The EKS rollback case called `update-nodegroup-version` then returned immediately without waiting for the nodegroup to become ACTIVE, leaving callers with no signal that rollback completed — a 30-minute poll loop identical to the upgrade path was added after the exec command; (2) The kubeadm default case issued `kubectl get nodes` with no selector, which would uncordon every node in the cluster including control-plane nodes and nodes belonging to other pools — this was replaced with the same label-first / control-plane-exclusion fallback pattern already used by the upgrade path, scoping uncordon to only the target pool's nodes. Both fixes are covered by the existing test suite; all 7 tests pass after the changes (build time 0.780s).

## Concerns
- The brief's `upgradeKubeadmNodes` drain logic had a slice indexing bug (`drainArgs[len([]string{...}):]`) that would panic at runtime. Fixed by passing drain args directly to `kube()` without the slice-offset trick.
- kubeadm rollback is a no-op for version downgrade (uncordon only) — documented in the function comment and response payload.
