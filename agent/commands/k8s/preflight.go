// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

package k8s

import (
	"fmt"
	"time"
)

// PreflightExecute checks cluster readiness for a one-minor-version upgrade.
// Params: target_version (string, required), cluster_type (string, optional: eks|gke|aks|kubeadm|auto),
//
//	kubeconfig_b64 (string, optional), kubeconfig_path (string, optional).
//
// Returns: status ("ok"|"blocked"), cluster_type, current_version, target_version, node_pools, removed_api_violations, warnings.
func PreflightExecute(params map[string]any) (map[string]any, error) {
	targetVersion, _ := params["target_version"].(string)
	if targetVersion == "" {
		return nil, fmt.Errorf("target_version required (e.g. '1.29')")
	}

	kubeconfigPath, cleanup, err := writeKubeconfig(params)
	if err != nil {
		return nil, err
	}
	defer cleanup()

	// Detect cluster type
	clusterType, _ := params["cluster_type"].(string)
	if clusterType == "" || clusterType == "auto" {
		clusterType = detectClusterType(kubeconfigPath)
	}

	// Get current server version — kubectl version exits non-zero when client/server differ; ignore that.
	versionJSON, _ := kube(kubeconfigPath, "version", "--output=json")
	currentVersion := parseServerVersion(versionJSON)
	if currentVersion == "unknown" {
		return nil, fmt.Errorf("kubectl version failed — server unreachable or output not JSON: %s", versionJSON)
	}

	// Enforce +1 minor skew
	if skewErr := validateVersionSkew(currentVersion, targetVersion); skewErr != nil {
		return map[string]any{
			"status":          "blocked",
			"reason":          skewErr.Error(),
			"current_version": currentVersion,
			"target_version":  targetVersion,
			"node_pools":      []map[string]any{},
			"warnings":        []string{},
		}, nil
	}

	// List nodes → node pools — ignore non-zero exit (e.g. warnings on stderr); only fail if output is empty.
	nodesJSON, _ := kube(kubeconfigPath, "get", "nodes", "--output=json")
	if nodesJSON == "" {
		return nil, fmt.Errorf("kubectl get nodes returned empty output — cluster unreachable?")
	}
	nodePools := buildNodePools(nodesJSON, clusterType)

	// Check for removed APIs
	removedViolations := scanRemovedAPIs(kubeconfigPath, targetVersion)

	warnings := []string{}
	if len(removedViolations) > 0 {
		warnings = append(warnings,
			fmt.Sprintf("%d deprecated API violation(s) found — resolve before upgrading", len(removedViolations)))
	}

	return map[string]any{
		"status":                 "ok",
		"cluster_type":           clusterType,
		"current_version":        currentVersion,
		"target_version":         targetVersion,
		"node_pools":             nodePools,
		"removed_api_violations": removedViolations,
		"warnings":               warnings,
		"checked_at":             time.Now().UTC().Format(time.RFC3339),
	}, nil
}
