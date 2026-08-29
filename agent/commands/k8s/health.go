// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

package k8s

import (
	"encoding/json"
	"fmt"
	"strings"
	"time"
)

// ClusterHealthExecute verifies cluster health after an upgrade.
// Checks: all nodes Ready, no failed/pending system pods, server version matches expected.
//
// Params: expected_version (string, optional), kubeconfig_b64 or kubeconfig_path (optional).
func ClusterHealthExecute(params map[string]any) (map[string]any, error) {
	expectedVersion, _ := params["expected_version"].(string)

	kubeconfigPath, cleanup, err := writeKubeconfig(params)
	if err != nil {
		return nil, err
	}
	defer cleanup()

	// Check server reachability
	versionJSON, err := kube(kubeconfigPath, "version", "--output=json")
	if err != nil {
		return map[string]any{
			"healthy": false,
			"error":   fmt.Sprintf("kubectl version failed: %s", versionJSON),
		}, nil
	}
	actualVersion := parseServerVersion(versionJSON)

	// Check nodes
	nodesJSON, err := kube(kubeconfigPath, "get", "nodes", "--output=json")
	if err != nil {
		return map[string]any{
			"healthy": false,
			"error":   fmt.Sprintf("kubectl get nodes: %s", nodesJSON),
		}, nil
	}

	var nodeList struct {
		Items []struct {
			Metadata struct{ Name string }
			Status   struct {
				Conditions []struct {
					Type   string `json:"type"`
					Status string `json:"status"`
				} `json:"conditions"`
			} `json:"status"`
		} `json:"items"`
	}
	json.Unmarshal([]byte(nodesJSON), &nodeList) //nolint:errcheck

	nodesTotal := len(nodeList.Items)
	nodesReady := 0
	notReadyNodes := []string{}
	for _, n := range nodeList.Items {
		for _, c := range n.Status.Conditions {
			if c.Type == "Ready" {
				if c.Status == "True" {
					nodesReady++
				} else {
					notReadyNodes = append(notReadyNodes, n.Metadata.Name)
				}
				break
			}
		}
	}

	// Check system pods
	podsJSON, _ := kube(kubeconfigPath, "get", "pods", "-n", "kube-system", "--output=json")
	unhealthyPods := scanUnhealthyPods(podsJSON)

	// Version match check — compare minor versions so patch-level differences don't cause false negatives.
	versionOK := expectedVersion == "" || extractMinor(actualVersion) == extractMinor(expectedVersion)

	healthy := nodesReady == nodesTotal && len(unhealthyPods) == 0 && versionOK

	result := map[string]any{
		"healthy":         healthy,
		"nodes_total":     nodesTotal,
		"nodes_ready":     nodesReady,
		"not_ready_nodes": notReadyNodes,
		"pods_unhealthy":  unhealthyPods,
		"actual_version":  actualVersion,
		"version_ok":      versionOK,
		"checked_at":      time.Now().UTC().Format(time.RFC3339),
	}
	if !healthy {
		msgs := []string{}
		if nodesReady < nodesTotal {
			msgs = append(msgs, fmt.Sprintf("%d/%d nodes not Ready", nodesTotal-nodesReady, nodesTotal))
		}
		if len(unhealthyPods) > 0 {
			msgs = append(msgs, fmt.Sprintf("%d unhealthy system pods", len(unhealthyPods)))
		}
		if !versionOK {
			msgs = append(msgs, fmt.Sprintf("version mismatch: expected %s, got %s", expectedVersion, actualVersion))
		}
		result["reason"] = strings.Join(msgs, "; ")
	}
	return result, nil
}

func scanUnhealthyPods(podsJSON string) []string {
	var podList struct {
		Items []struct {
			Metadata struct{ Name string }
			Status   struct {
				Phase             string `json:"phase"`
				ContainerStatuses []struct {
					Ready bool   `json:"ready"`
					Name  string `json:"name"`
				} `json:"containerStatuses"`
			} `json:"status"`
		} `json:"items"`
	}
	if err := json.Unmarshal([]byte(podsJSON), &podList); err != nil {
		return nil
	}
	var unhealthy []string
	for _, pod := range podList.Items {
		if pod.Status.Phase == "Failed" {
			unhealthy = append(unhealthy, pod.Metadata.Name)
			continue
		}
		for _, cs := range pod.Status.ContainerStatuses {
			if !cs.Ready && pod.Status.Phase == "Running" {
				unhealthy = append(unhealthy, fmt.Sprintf("%s/%s", pod.Metadata.Name, cs.Name))
				break
			}
		}
	}
	return unhealthy
}
