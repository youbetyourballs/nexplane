// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

package k8s

import (
	"testing"
)

func TestExtractMinor(t *testing.T) {
	cases := []struct{ version string; want int }{
		{"1.28.13", 28},
		{"v1.29.4", 29},
		{"1.30.0-eks-1234abc", 30},
		{"unknown", -1},
	}
	for _, c := range cases {
		got := extractMinor(c.version)
		if got != c.want {
			t.Errorf("extractMinor(%q) = %d, want %d", c.version, got, c.want)
		}
	}
}

func TestValidateVersionSkew(t *testing.T) {
	if err := validateVersionSkew("1.28.13", "1.29"); err != nil {
		t.Errorf("expected no error for +1 minor, got: %v", err)
	}
	if err := validateVersionSkew("1.28.13", "1.30"); err == nil {
		t.Error("expected error for +2 minor skew")
	}
	if err := validateVersionSkew("1.28.13", "1.27"); err == nil {
		t.Error("expected error for downgrade")
	}
}

func TestDetectClusterType(t *testing.T) {
	// detectClusterType is tested via server URL pattern matching.
	// We test the underlying logic through exposed helper.
	cases := []struct {
		serverURL string
		want      string
	}{
		{"https://ABC123.gr7.us-east-1.eks.amazonaws.com", "eks"},
		{"https://container.googleapis.com/v1/projects/my-project/zones/us-central1-a/clusters/my-cluster", "gke"},
		{"https://my-cluster-abc123.hcp.eastus.azmk8s.io", "aks"},
		{"https://192.168.1.100:6443", "kubeadm"},
	}
	for _, c := range cases {
		got := clusterTypeFromURL(c.serverURL)
		if got != c.want {
			t.Errorf("clusterTypeFromURL(%q) = %q, want %q", c.serverURL, got, c.want)
		}
	}
}

func TestBuildNodePoolsKubeadm(t *testing.T) {
	nodesJSON := `{
		"items": [
			{
				"metadata": {
					"name": "worker-1",
					"labels": {"kubernetes.io/hostname": "worker-1"}
				},
				"status": {"nodeInfo": {"kubeletVersion": "v1.28.13"}},
				"spec": {"taints": []}
			},
			{
				"metadata": {
					"name": "worker-2",
					"labels": {"kubernetes.io/hostname": "worker-2"}
				},
				"status": {"nodeInfo": {"kubeletVersion": "v1.28.13"}},
				"spec": {"taints": []}
			},
			{
				"metadata": {
					"name": "control-plane-1",
					"labels": {
						"kubernetes.io/hostname": "control-plane-1",
						"node-role.kubernetes.io/control-plane": ""
					}
				},
				"status": {"nodeInfo": {"kubeletVersion": "v1.28.13"}},
				"spec": {"taints": [{"effect": "NoSchedule", "key": "node-role.kubernetes.io/control-plane"}]}
			}
		]
	}`
	pools := buildNodePools(nodesJSON, "kubeadm")
	if len(pools) != 1 {
		t.Fatalf("expected 1 pool, got %d: %v", len(pools), pools)
	}
	if pools[0]["name"] != "workers" {
		t.Errorf("expected pool name 'workers', got %q", pools[0]["name"])
	}
	if pools[0]["node_count"] != 2 {
		t.Errorf("expected node_count 2, got %v", pools[0]["node_count"])
	}
}
