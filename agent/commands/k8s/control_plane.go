// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

package k8s

import (
	"fmt"
	"os/exec"
	"strings"
	"time"
)

// ControlPlaneUpgradeExecute upgrades the k8s control plane to the target version.
// For managed clusters (EKS/GKE/AKS): calls the cloud provider CLI.
// For kubeadm: runs `kubeadm upgrade apply`.
// NOTE: Control plane upgrades are irreversible. K8s does not support control plane downgrade.
//
// Params: target_version (required), cluster_type (required: eks|gke|aks|kubeadm),
//
//	cluster_name, region (EKS), location (GKE), resource_group (AKS),
//	kubeconfig_b64 or kubeconfig_path (optional).
func ControlPlaneUpgradeExecute(params map[string]any) (map[string]any, error) {
	targetVersion, _ := params["target_version"].(string)
	if targetVersion == "" {
		return nil, fmt.Errorf("target_version required")
	}
	clusterType, _ := params["cluster_type"].(string)
	if clusterType == "" {
		return nil, fmt.Errorf("cluster_type required (eks|gke|aks|kubeadm)")
	}

	kubeconfigPath, cleanup, err := writeKubeconfig(params)
	if err != nil {
		return nil, err
	}
	defer cleanup()

	var rawOut []byte
	var execErr error

	switch clusterType {
	case "eks":
		clusterName, _ := params["cluster_name"].(string)
		if clusterName == "" {
			clusterName, _ = kube(kubeconfigPath, "config", "view", "--minify",
				"--output=jsonpath={.contexts[0].context.cluster}")
		}
		region, _ := params["region"].(string)
		if region == "" {
			region = "us-east-1"
		}
		rawOut, execErr = exec.Command("aws", "eks", "update-cluster-version",
			"--name", clusterName,
			"--kubernetes-version", targetVersion,
			"--region", region).CombinedOutput()
		if execErr != nil {
			return nil, fmt.Errorf("aws eks update-cluster-version: %s", rawOut)
		}
		// Poll until cluster ACTIVE (managed upgrades are async)
		deadline := time.Now().Add(35 * time.Minute)
		for time.Now().Before(deadline) {
			statusOut, _ := exec.Command("aws", "eks", "describe-cluster",
				"--name", clusterName, "--region", region,
				"--query", "cluster.status", "--output", "text").CombinedOutput()
			if strings.TrimSpace(string(statusOut)) == "ACTIVE" {
				break
			}
			time.Sleep(30 * time.Second)
		}

	case "gke":
		clusterName, _ := params["cluster_name"].(string)
		location, _ := params["location"].(string)
		if location == "" {
			location = "us-central1"
		}
		rawOut, execErr = exec.Command("gcloud", "container", "clusters", "upgrade",
			clusterName, "--location", location,
			"--master", "--cluster-version", targetVersion, "--quiet").CombinedOutput()
		if execErr != nil {
			return nil, fmt.Errorf("gcloud container clusters upgrade: %s", rawOut)
		}

	case "aks":
		clusterName, _ := params["cluster_name"].(string)
		resourceGroup, _ := params["resource_group"].(string)
		if resourceGroup == "" {
			return nil, fmt.Errorf("resource_group required for cluster_type=aks")
		}
		rawOut, execErr = exec.Command("az", "aks", "upgrade",
			"--resource-group", resourceGroup,
			"--name", clusterName,
			"--kubernetes-version", targetVersion,
			"--control-plane-only", "--yes").CombinedOutput()
		if execErr != nil {
			return nil, fmt.Errorf("az aks upgrade: %s", rawOut)
		}

	case "kubeadm":
		// Install target kubeadm version
		aptPkg := "kubeadm=" + targetVersion + "-*"
		exec.Command("apt-get", "install", "-y", "--allow-change-held-packages",
			aptPkg).Run() //nolint:errcheck

		rawOut, execErr = exec.Command("kubeadm", "upgrade", "apply",
			"v"+targetVersion, "--yes").CombinedOutput()
		if execErr != nil {
			return nil, fmt.Errorf("kubeadm upgrade apply: %s", rawOut)
		}

	default:
		return nil, fmt.Errorf("unknown cluster_type %q (use eks|gke|aks|kubeadm)", clusterType)
	}

	return map[string]any{
		"success":        true,
		"cluster_type":   clusterType,
		"target_version": targetVersion,
		"output":         strings.TrimSpace(string(rawOut)),
		"upgraded_at":    time.Now().UTC().Format(time.RFC3339),
		"note":           "Control plane upgrade is irreversible — k8s does not support control plane downgrade",
	}, nil
}
