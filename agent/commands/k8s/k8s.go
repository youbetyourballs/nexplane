// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

package k8s

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"strings"
)

// writeKubeconfig writes kubeconfig_b64 param to a temp file.
// Returns the path (empty string = use ambient KUBECONFIG), a cleanup func, and any error.
func writeKubeconfig(params map[string]any) (string, func(), error) {
	if p, _ := params["kubeconfig_path"].(string); p != "" {
		return p, func() {}, nil
	}
	b64, _ := params["kubeconfig_b64"].(string)
	if b64 == "" {
		return "", func() {}, nil
	}
	decoded, err := base64.StdEncoding.DecodeString(b64)
	if err != nil {
		return "", func() {}, fmt.Errorf("kubeconfig_b64 decode: %w", err)
	}
	f, err := os.CreateTemp("", "nexplane-kube-*.yaml")
	if err != nil {
		return "", func() {}, fmt.Errorf("create temp kubeconfig: %w", err)
	}
	if _, err = f.Write(decoded); err != nil {
		f.Close()
		os.Remove(f.Name())
		return "", func() {}, fmt.Errorf("write kubeconfig: %w", err)
	}
	f.Close()
	name := f.Name()
	return name, func() { os.Remove(name) }, nil
}

// kube runs kubectl with optional kubeconfig override and returns combined output.
func kube(kubeconfigPath string, args ...string) (string, error) {
	cmd := exec.Command("kubectl", args...)
	if kubeconfigPath != "" {
		cmd.Env = append(os.Environ(), "KUBECONFIG="+kubeconfigPath)
	}
	out, err := cmd.CombinedOutput()
	return strings.TrimSpace(string(out)), err
}

// clusterTypeFromURL classifies a k8s server URL into "eks", "gke", "aks", or "kubeadm".
func clusterTypeFromURL(serverURL string) string {
	switch {
	case strings.Contains(serverURL, ".eks.amazonaws.com"):
		return "eks"
	case strings.Contains(serverURL, ".gke.io") ||
		strings.Contains(serverURL, "container.googleapis.com"):
		return "gke"
	case strings.Contains(serverURL, ".azmk8s.io") ||
		(strings.Contains(serverURL, "azure.com") && strings.Contains(serverURL, "hcp")):
		return "aks"
	default:
		return "kubeadm"
	}
}

// detectClusterType queries the active kubeconfig's server URL to detect cluster type.
func detectClusterType(kubeconfigPath string) string {
	out, err := kube(kubeconfigPath, "config", "view", "--minify",
		"--output=jsonpath={.clusters[0].cluster.server}")
	if err != nil || out == "" {
		return "kubeadm"
	}
	return clusterTypeFromURL(out)
}

// parseServerVersion extracts the server gitVersion from `kubectl version -o json` output.
func parseServerVersion(jsonOut string) string {
	var v struct {
		ServerVersion struct {
			GitVersion string `json:"gitVersion"`
		} `json:"serverVersion"`
	}
	if err := json.Unmarshal([]byte(jsonOut), &v); err != nil {
		return "unknown"
	}
	return strings.TrimPrefix(v.ServerVersion.GitVersion, "v")
}

// validateVersionSkew enforces +1 minor version constraint.
func validateVersionSkew(current, target string) error {
	currentMinor := extractMinor(current)
	targetMinor := extractMinor(target)
	if currentMinor < 0 || targetMinor < 0 {
		return nil // can't parse, allow through with a warning
	}
	if targetMinor != currentMinor+1 {
		return fmt.Errorf(
			"k8s only supports upgrading one minor version at a time; current=%s target=%s (want minor %d, got %d)",
			current, target, currentMinor+1, targetMinor,
		)
	}
	return nil
}

// extractMinor parses the minor version number from a k8s version string like "1.28.13" or "v1.29.4".
func extractMinor(version string) int {
	parts := strings.Split(strings.TrimPrefix(version, "v"), ".")
	if len(parts) < 2 {
		return -1
	}
	// Strip any suffix like "-eks-1234abc"
	minorStr := strings.Split(parts[1], "-")[0]
	var minor int
	if _, err := fmt.Sscanf(minorStr, "%d", &minor); err != nil {
		return -1
	}
	return minor
}

type nodeItem struct {
	Metadata struct {
		Name   string            `json:"name"`
		Labels map[string]string `json:"labels"`
	} `json:"metadata"`
	Status struct {
		NodeInfo struct {
			KubeletVersion string `json:"kubeletVersion"`
		} `json:"nodeInfo"`
	} `json:"status"`
	Spec struct {
		Taints []struct {
			Effect string `json:"effect"`
			Key    string `json:"key"`
		} `json:"taints"`
	} `json:"spec"`
}

// buildNodePools parses kubectl get nodes JSON and groups worker nodes into pools.
func buildNodePools(nodesJSON, clusterType string) []map[string]any {
	var list struct{ Items []nodeItem }
	if err := json.Unmarshal([]byte(nodesJSON), &list); err != nil {
		return []map[string]any{}
	}

	// Determine pool label by cluster type
	poolLabel := map[string]string{
		"eks": "eks.amazonaws.com/nodegroup",
		"gke": "cloud.google.com/gke-nodepool",
		"aks": "agentpool",
	}[clusterType]

	poolNodes := map[string][]string{}
	poolVersion := map[string]string{}

	for _, n := range list.Items {
		// Skip control-plane nodes
		if _, ok := n.Metadata.Labels["node-role.kubernetes.io/control-plane"]; ok {
			continue
		}
		if _, ok := n.Metadata.Labels["node-role.kubernetes.io/master"]; ok {
			continue
		}

		pool := "workers"
		if poolLabel != "" {
			if pl, ok := n.Metadata.Labels[poolLabel]; ok && pl != "" {
				pool = pl
			}
		}
		poolNodes[pool] = append(poolNodes[pool], n.Metadata.Name)
		if _, seen := poolVersion[pool]; !seen {
			poolVersion[pool] = n.Status.NodeInfo.KubeletVersion
		}
	}

	result := make([]map[string]any, 0, len(poolNodes))
	for name, nodes := range poolNodes {
		result = append(result, map[string]any{
			"name":          name,
			"image_version": poolVersion[name],
			"nodes":         nodes,
			"node_count":    len(nodes),
		})
	}
	return result
}

// scanRemovedAPIs checks for resources using APIs removed at or before targetVersion minor.
// Returns a slice of human-readable violation strings.
func scanRemovedAPIs(kubeconfigPath, targetVersion string) []string {
	type removedAPI struct {
		group, version, resource string
	}
	// APIs removed in each minor version (check if target >= that minor)
	removedByMinor := map[int][]removedAPI{
		25: {
			{"policy", "v1beta1", "poddisruptionbudgets"},
			{"batch", "v1beta1", "cronjobs"},
		},
		26: {
			{"autoscaling", "v2beta2", "horizontalpodautoscalers"},
		},
		27: {
			{"storage.k8s.io", "v1beta1", "csinodes"},
		},
		29: {
			{"flowcontrol.apiserver.k8s.io", "v1beta2", "flowschemas"},
		},
	}

	targetMinor := extractMinor(targetVersion)
	if targetMinor < 0 {
		return nil
	}

	var violations []string
	for minor, apis := range removedByMinor {
		if minor > targetMinor {
			continue
		}
		for _, api := range apis {
			out, err := kube(kubeconfigPath, "get", api.resource,
				"--all-namespaces", "--output=name", "--ignore-not-found")
			if err == nil && strings.TrimSpace(out) != "" {
				violations = append(violations,
					fmt.Sprintf("%s/%s %s", api.group, api.version, api.resource))
			}
		}
	}
	return violations
}
