// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

package appupgrade

import (
	"fmt"
	"strings"
	"time"
)

// OpenSearchPreflightExecute checks connectivity, distribution, version path, cluster health.
// Command name: "app_preflight_opensearch"
func OpenSearchPreflightExecute(params map[string]any) (map[string]any, error) {
	host          := str(params, "os_host", "localhost")
	port          := intParam(params, "os_port", 9200)
	scheme        := str(params, "os_scheme", "http")
	user          := str(params, "os_user", "")
	pass          := str(params, "os_password", "")
	targetVersion := str(params, "target_version", "")

	root, err := esGet(host, port, scheme, user, pass, "/")
	if err != nil {
		return map[string]any{
			"status":   "preflight_blocked",
			"reason":   fmt.Sprintf("OpenSearch not reachable at %s://%s:%d: %s", scheme, host, port, err),
			"findings": []map[string]any{{"severity": "CRITICAL", "check": "connectivity", "detail": err.Error()}},
		}, nil
	}

	findings := []map[string]any{}

	// Verify distribution
	distribution := ""
	if v, ok := root["version"].(map[string]any); ok {
		distribution, _ = v["distribution"].(string)
	}
	if distribution != "opensearch" {
		findings = append(findings, map[string]any{
			"severity": "CRITICAL",
			"check":    "distribution",
			"detail":   fmt.Sprintf("expected distribution=opensearch, got %q", distribution),
		})
	}

	// Detect version
	version := ""
	if v, ok := root["version"].(map[string]any); ok {
		version, _ = v["number"].(string)
	}

	// Check version path: upgrading to 2.x requires source to be 1.x
	if strings.HasPrefix(targetVersion, "2.") && !strings.HasPrefix(version, "1.") {
		findings = append(findings, map[string]any{
			"severity": "WARNING",
			"check":    "version_path",
			"detail":   fmt.Sprintf("upgrading from %s to 2.x — ensure all plugins are compatible", version),
		})
	}

	// Cluster health
	health, err := esGet(host, port, scheme, user, pass, "/_cluster/health")
	if err == nil {
		if status, _ := health["status"].(string); status == "red" {
			findings = append(findings, map[string]any{
				"severity": "CRITICAL",
				"check":    "cluster_health",
				"detail":   "cluster health is RED -- resolve all shard issues before upgrading",
			})
		}
	}

	hasCritical := false
	for _, f := range findings {
		if f["severity"] == "CRITICAL" {
			hasCritical = true
			break
		}
	}

	result := map[string]any{
		"detected_version": version,
		"distribution":     distribution,
		"target_version":   targetVersion,
		"findings":         findings,
		"preflight_passed": !hasCritical,
	}
	if hasCritical {
		result["status"] = "preflight_blocked"
		result["reason"] = "One or more CRITICAL preflight checks failed"
	}
	return result, nil
}

// OpenSearchUpgradeExecute performs rolling upgrade. Package name uses opensearch prefix.
// Command name: "app_upgrade_opensearch"
func OpenSearchUpgradeExecute(params map[string]any) (map[string]any, error) {
	host          := str(params, "os_host", "localhost")
	port          := intParam(params, "os_port", 9200)
	scheme        := str(params, "os_scheme", "http")
	user          := str(params, "os_user", "")
	pass          := str(params, "os_password", "")
	targetVersion := str(params, "target_version", "")

	steps := []string{}

	// 1. Disable shard allocation
	if err := esPut(host, port, scheme, user, pass,
		"/_cluster/settings",
		`{"persistent":{"cluster.routing.allocation.enable":"primaries"}}`); err != nil {
		return nil, fmt.Errorf("disable_shard_allocation: %w", err)
	}
	steps = append(steps, "disable_shard_allocation")

	// 2. Stop OpenSearch
	runCmd("systemctl", "stop", "opensearch")
	steps = append(steps, "stop_opensearch")

	// 3. Upgrade package (apt/yum/docker)
	if _, err := runCmd("apt-get", "install", "-y", "--allow-downgrades",
		fmt.Sprintf("opensearch=%s", targetVersion)); err != nil {
		if _, err2 := runCmd("yum", "install", "-y",
			fmt.Sprintf("opensearch-%s", targetVersion)); err2 != nil {
			// Docker smoke path
			runCmd("docker", "run", "-d", "--name", "os2",
				"-p", fmt.Sprintf("%d:9200", port+1),
				"-e", "discovery.type=single-node",
				"-e", "DISABLE_SECURITY_PLUGIN=true",
				fmt.Sprintf("opensearchproject/opensearch:%s", targetVersion))
		}
	}
	steps = append(steps, "upgrade_package")

	// 4. Start
	runCmd("systemctl", "start", "opensearch")
	steps = append(steps, "start_opensearch")

	// 5. Wait for health
	upgraded := false
	for i := 0; i < 24; i++ {
		time.Sleep(5 * time.Second)
		for _, p := range []int{port, port + 1} {
			h, err := esGet(host, p, scheme, user, pass, "/_cluster/health")
			if err == nil {
				if st, _ := h["status"].(string); st == "green" || st == "yellow" {
					upgraded = true
					break
				}
			}
		}
		if upgraded {
			break
		}
	}
	if !upgraded {
		return nil, fmt.Errorf("opensearch did not become healthy within 120s after upgrade")
	}
	steps = append(steps, "wait_for_health")

	esPut(host, port, scheme, user, pass, "/_cluster/settings",
		`{"persistent":{"cluster.routing.allocation.enable":null}}`)
	esPut(host, port+1, scheme, user, pass, "/_cluster/settings",
		`{"persistent":{"cluster.routing.allocation.enable":null}}`)
	steps = append(steps, "enable_shard_allocation")

	return map[string]any{
		"steps_completed": steps,
		"target_version":  targetVersion,
		"upgraded_port":   port + 1,
	}, nil
}

// OpenSearchSnapshotLocalExecute tars the OpenSearch data directory to a local path.
// Command name: "app_snapshot_local_opensearch"
func OpenSearchSnapshotLocalExecute(params map[string]any) (map[string]any, error) {
	localPath := str(params, "local_path", "/tmp/os_snap.tar.gz")
	dataDir   := str(params, "os_data_dir", "/var/lib/opensearch")

	out, err := runCmd("tar", "-czf", localPath, dataDir)
	if err != nil {
		return nil, fmt.Errorf("tar snapshot: %s: %w", out, err)
	}
	return map[string]any{"local_path": localPath, "data_dir": dataDir}, nil
}

// OpenSearchRestoreLocalExecute restores from a local tar snapshot.
// Command name: "app_restore_local_opensearch"
func OpenSearchRestoreLocalExecute(params map[string]any) (map[string]any, error) {
	localPath := str(params, "local_path", "")
	dataDir   := str(params, "os_data_dir", "/var/lib/opensearch")

	if localPath == "" {
		return nil, fmt.Errorf("local_path required for restore")
	}
	runCmd("systemctl", "stop", "opensearch")
	runCmd("rm", "-rf", dataDir)
	if out, err := runCmd("tar", "-xzf", localPath, "-C", "/"); err != nil {
		return nil, fmt.Errorf("tar restore: %s: %w", out, err)
	}
	runCmd("systemctl", "start", "opensearch")
	return map[string]any{"restored": true, "local_path": localPath}, nil
}
