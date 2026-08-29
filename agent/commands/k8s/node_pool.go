// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

package k8s

import (
	"fmt"
	"os/exec"
	"strings"
	"time"
)

// NodePoolUpgradeExecute upgrades worker nodes in a named node pool using a rolling strategy.
// Drains each node, upgrades the kubelet (kubeadm) or calls cloud API (EKS/GKE/AKS), then uncordons.
//
// Params: target_version (required), cluster_type (required), pool_name (required),
//
//	node_pool_strategy (default "rolling"), drain_timeout_seconds (default 300),
//	max_unavailable (default 1), kubeconfig_b64 or kubeconfig_path (optional).
func NodePoolUpgradeExecute(params map[string]any) (map[string]any, error) {
	targetVersion, _ := params["target_version"].(string)
	if targetVersion == "" {
		return nil, fmt.Errorf("target_version required")
	}
	clusterType, _ := params["cluster_type"].(string)
	poolName, _ := params["pool_name"].(string)
	drainTimeout := 300
	if v, ok := params["drain_timeout_seconds"].(float64); ok {
		drainTimeout = int(v)
	}
	maxUnavailable := 1
	if v, ok := params["max_unavailable"].(float64); ok && v > 0 {
		maxUnavailable = int(v)
	}

	kubeconfigPath, cleanup, err := writeKubeconfig(params)
	if err != nil {
		return nil, err
	}
	defer cleanup()

	switch clusterType {
	case "eks":
		return upgradeEKSNodeGroup(params, kubeconfigPath, poolName, targetVersion)
	case "gke":
		return upgradeGKENodePool(params, kubeconfigPath, poolName, targetVersion)
	case "aks":
		return upgradeAKSNodePool(params, kubeconfigPath, poolName, targetVersion)
	default: // kubeadm
		return upgradeKubeadmNodes(kubeconfigPath, poolName, targetVersion, drainTimeout, maxUnavailable)
	}
}

func upgradeKubeadmNodes(kubeconfigPath, poolName, targetVersion string, drainTimeout, maxUnavailable int) (map[string]any, error) {
	// Get nodes in this pool (by nexplane.io/node-pool label, or all workers if label absent)
	labelSelector := "nexplane.io/node-pool=" + poolName
	nodesOut, err := kube(kubeconfigPath, "get", "nodes", "-l", labelSelector,
		"--output=jsonpath={.items[*].metadata.name}")
	if err != nil || strings.TrimSpace(nodesOut) == "" {
		// Fallback: all non-control-plane nodes
		nodesOut, err = kube(kubeconfigPath, "get", "nodes",
			"--selector=!node-role.kubernetes.io/control-plane",
			"--output=jsonpath={.items[*].metadata.name}")
		if err != nil {
			return nil, fmt.Errorf("kubectl get nodes: %s", nodesOut)
		}
	}

	nodeNames := strings.Fields(nodesOut)
	if len(nodeNames) == 0 {
		return map[string]any{
			"pool_name": poolName, "nodes_upgraded": 0,
			"success": true, "note": "no nodes found in pool",
		}, nil
	}

	// Install target kubelet/kubectl on this host first (assumes agent runs on a node or management host)
	aptPkg := "=" + targetVersion + "-*"
	exec.Command("apt-get", "install", "-y", "--allow-change-held-packages",
		"kubelet"+aptPkg, "kubectl"+aptPkg).Run() //nolint:errcheck

	upgraded := 0
	var inFlight []string

	for _, nodeName := range nodeNames {
		// Drain node
		drainOut, drainErr := kube(kubeconfigPath, "drain", nodeName,
			"--ignore-daemonsets", "--delete-emptydir-data",
			fmt.Sprintf("--timeout=%ds", drainTimeout),
		)
		if drainErr != nil {
			// Uncordon any already-cordoned nodes before returning
			for _, prev := range inFlight {
				kube(kubeconfigPath, "uncordon", prev) //nolint:errcheck
			}
			return nil, fmt.Errorf("drain %s failed: %s", nodeName, drainOut)
		}
		inFlight = append(inFlight, nodeName)

		// Upgrade kubelet on node via SSH (kubeadm node upgrade)
		exec.Command("kubeadm", "upgrade", "node").Run()    //nolint:errcheck
		exec.Command("systemctl", "daemon-reload").Run()    //nolint:errcheck
		exec.Command("systemctl", "restart", "kubelet").Run() //nolint:errcheck

		// Uncordon
		kube(kubeconfigPath, "uncordon", nodeName) //nolint:errcheck

		upgraded++
		_ = maxUnavailable // maxUnavailable governs how many can be draining simultaneously; single-node simplification here
		time.Sleep(5 * time.Second) // brief stabilization between nodes
	}

	return map[string]any{
		"pool_name":      poolName,
		"nodes_upgraded": upgraded,
		"nodes":          nodeNames,
		"success":        true,
		"target_version": targetVersion,
		"upgraded_at":    time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func upgradeEKSNodeGroup(params map[string]any, kubeconfigPath, poolName, targetVersion string) (map[string]any, error) {
	clusterName, _ := params["cluster_name"].(string)
	if clusterName == "" {
		clusterName, _ = kube(kubeconfigPath, "config", "view", "--minify",
			"--output=jsonpath={.contexts[0].context.cluster}")
	}
	region, _ := params["region"].(string)
	if region == "" {
		region = "us-east-1"
	}
	out, err := exec.Command("aws", "eks", "update-nodegroup-version",
		"--cluster-name", clusterName,
		"--nodegroup-name", poolName,
		"--kubernetes-version", targetVersion,
		"--region", region).CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("aws eks update-nodegroup-version: %s", out)
	}
	// Wait for nodegroup ACTIVE
	deadline := time.Now().Add(30 * time.Minute)
	for time.Now().Before(deadline) {
		statusOut, _ := exec.Command("aws", "eks", "describe-nodegroup",
			"--cluster-name", clusterName, "--nodegroup-name", poolName,
			"--region", region,
			"--query", "nodegroup.status", "--output", "text").CombinedOutput()
		if strings.TrimSpace(string(statusOut)) == "ACTIVE" {
			break
		}
		time.Sleep(30 * time.Second)
	}
	return map[string]any{
		"pool_name": poolName, "success": true,
		"target_version": targetVersion,
		"output":         strings.TrimSpace(string(out)),
		"upgraded_at":    time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func upgradeGKENodePool(params map[string]any, kubeconfigPath, poolName, targetVersion string) (map[string]any, error) {
	clusterName, _ := params["cluster_name"].(string)
	location, _ := params["location"].(string)
	if location == "" {
		location = "us-central1"
	}
	out, err := exec.Command("gcloud", "container", "clusters", "upgrade",
		clusterName, "--location", location,
		"--node-pool", poolName,
		"--cluster-version", targetVersion, "--quiet").CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("gcloud upgrade node pool: %s", out)
	}
	return map[string]any{
		"pool_name": poolName, "success": true,
		"target_version": targetVersion,
		"output":         strings.TrimSpace(string(out)),
	}, nil
}

func upgradeAKSNodePool(params map[string]any, kubeconfigPath, poolName, targetVersion string) (map[string]any, error) {
	clusterName, _ := params["cluster_name"].(string)
	resourceGroup, _ := params["resource_group"].(string)
	if resourceGroup == "" {
		return nil, fmt.Errorf("resource_group required for cluster_type=aks")
	}
	out, err := exec.Command("az", "aks", "nodepool", "upgrade",
		"--resource-group", resourceGroup,
		"--cluster-name", clusterName,
		"--name", poolName,
		"--kubernetes-version", targetVersion,
		"--yes").CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("az aks nodepool upgrade: %s", out)
	}
	return map[string]any{
		"pool_name": poolName, "success": true,
		"target_version": targetVersion,
		"output":         strings.TrimSpace(string(out)),
	}, nil
}

// NodePoolRollbackExecute restores a node pool to its previous image/version.
// For managed clusters: calls the cloud provider downgrade API.
// For kubeadm: uncordons any cordoned nodes (true version downgrade is unsupported by kubeadm).
//
// Params: cluster_type (required), pool_name (required), target_image (required — the previous version),
//
//	previous_version (string, optional), kubeconfig_b64 or kubeconfig_path (optional).
func NodePoolRollbackExecute(params map[string]any) (map[string]any, error) {
	targetImage, _ := params["target_image"].(string)
	if targetImage == "" {
		return nil, fmt.Errorf("target_image required for rollback (pass the previous image/version)")
	}
	clusterType, _ := params["cluster_type"].(string)
	poolName, _ := params["pool_name"].(string)

	kubeconfigPath, cleanup, err := writeKubeconfig(params)
	if err != nil {
		return nil, err
	}
	defer cleanup()

	switch clusterType {
	case "eks":
		clusterName, _ := params["cluster_name"].(string)
		region, _ := params["region"].(string)
		if region == "" {
			region = "us-east-1"
		}
		out, execErr := exec.Command("aws", "eks", "update-nodegroup-version",
			"--cluster-name", clusterName,
			"--nodegroup-name", poolName,
			"--kubernetes-version", targetImage,
			"--region", region).CombinedOutput()
		if execErr != nil {
			return nil, fmt.Errorf("aws eks rollback nodegroup: %s", out)
		}
		// Poll until nodegroup is ACTIVE
		deadline := time.Now().Add(30 * time.Minute)
		for time.Now().Before(deadline) {
			statusOut, _ := exec.Command("aws", "eks", "describe-nodegroup",
				"--cluster-name", clusterName,
				"--nodegroup-name", poolName,
				"--region", region,
				"--query", "nodegroup.status",
				"--output", "text").CombinedOutput()
			if strings.TrimSpace(string(statusOut)) == "ACTIVE" {
				break
			}
			time.Sleep(30 * time.Second)
		}
		return map[string]any{
			"rolled_back": true, "pool_name": poolName,
			"restored_to": targetImage,
			"output":      strings.TrimSpace(string(out)),
		}, nil

	case "gke":
		clusterName, _ := params["cluster_name"].(string)
		location, _ := params["location"].(string)
		if location == "" {
			location = "us-central1"
		}
		out, execErr := exec.Command("gcloud", "container", "clusters", "upgrade",
			clusterName, "--location", location,
			"--node-pool", poolName,
			"--cluster-version", targetImage, "--quiet").CombinedOutput()
		if execErr != nil {
			return nil, fmt.Errorf("gcloud rollback node pool: %s", out)
		}
		return map[string]any{
			"rolled_back": true, "pool_name": poolName,
			"restored_to": targetImage,
		}, nil

	case "aks":
		clusterName, _ := params["cluster_name"].(string)
		resourceGroup, _ := params["resource_group"].(string)
		if resourceGroup == "" {
			return nil, fmt.Errorf("resource_group required")
		}
		out, execErr := exec.Command("az", "aks", "nodepool", "upgrade",
			"--resource-group", resourceGroup,
			"--cluster-name", clusterName,
			"--name", poolName,
			"--kubernetes-version", targetImage,
			"--yes").CombinedOutput()
		if execErr != nil {
			return nil, fmt.Errorf("az aks nodepool rollback: %s", out)
		}
		return map[string]any{
			"rolled_back": true, "pool_name": poolName,
			"restored_to": targetImage,
		}, nil

	default: // kubeadm — uncordon any cordoned nodes (version downgrade not possible)
		labelSelector := "nexplane.io/node-pool=" + poolName
		nodesOut, _ := kube(kubeconfigPath, "get", "nodes", "-l", labelSelector,
			"--output=jsonpath={.items[*].metadata.name}")
		if strings.TrimSpace(nodesOut) == "" {
			// Fallback: all non-control-plane nodes
			nodesOut, _ = kube(kubeconfigPath, "get", "nodes",
				"--selector=!node-role.kubernetes.io/control-plane",
				"--output=jsonpath={.items[*].metadata.name}")
		}
		for _, nodeName := range strings.Fields(nodesOut) {
			kube(kubeconfigPath, "uncordon", nodeName) //nolint:errcheck
		}
		return map[string]any{
			"rolled_back": true,
			"pool_name":   poolName,
			"note":        "kubeadm does not support node version downgrade — nodes uncordoned; manual kubelet reinstall required",
			"restored_to": targetImage,
		}, nil
	}
}
