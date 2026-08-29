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

## Concerns
- The brief's `upgradeKubeadmNodes` drain logic had a slice indexing bug (`drainArgs[len([]string{...}):]`) that would panic at runtime. Fixed by passing drain args directly to `kube()` without the slice-offset trick.
- kubeadm rollback is a no-op for version downgrade (uncordon only) — documented in the function comment and response payload.
