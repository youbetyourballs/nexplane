// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

package appupgrade

import (
	"fmt"
	"os"
	"strings"
)

// KafkaBridgePreflightExecute checks ZooKeeper + broker health before bridge phase.
// Command name: "app_preflight_kafka_bridge"
func KafkaBridgePreflightExecute(params map[string]any) (map[string]any, error) {
	container     := str(params, "kafka_container", "kafka")
	zkHost        := str(params, "kafka_zk_host", "localhost")
	zkPort        := intParam(params, "kafka_zk_port", 2181)
	kafkaPort     := intParam(params, "kafka_port", 9092)
	sourceVersion := str(params, "source_version", "")

	findings := []map[string]any{}

	// 1. Check ZooKeeper is reachable
	if _, err := runCmd("docker", "exec", container, "zookeeper-shell.sh",
		fmt.Sprintf("%s:%d", zkHost, zkPort), "ls", "/brokers/ids"); err != nil {
		findings = append(findings, map[string]any{
			"severity": "CRITICAL",
			"check":    "zookeeper_connectivity",
			"detail":   err.Error(),
		})
		return map[string]any{
			"preflight_passed": false,
			"detected_version": sourceVersion,
			"zk_host":         zkHost,
			"findings":        findings,
		}, nil
	}

	// 2. Check broker is healthy
	if _, err := runCmd("docker", "exec", container, "kafka-broker-api-versions.sh",
		"--bootstrap-server", fmt.Sprintf("localhost:%d", kafkaPort)); err != nil {
		findings = append(findings, map[string]any{
			"severity": "CRITICAL",
			"check":    "broker_health",
			"detail":   err.Error(),
		})
		return map[string]any{
			"preflight_passed": false,
			"detected_version": sourceVersion,
			"zk_host":         zkHost,
			"findings":        findings,
		}, nil
	}

	// 3. Check KRaft migration NOT already present (absence of /kraft-migration znode)
	out, _ := runCmd("docker", "exec", container, "zookeeper-shell.sh",
		fmt.Sprintf("%s:%d", zkHost, zkPort), "ls", "/kraft-migration")
	if strings.Contains(out, "kraft-migration") {
		findings = append(findings, map[string]any{
			"severity": "WARNING",
			"check":    "kraft_migration_znode",
			"detail":   "KRaft migration znode already exists — bridge may have already run",
		})
	}

	return map[string]any{
		"preflight_passed": true,
		"detected_version": sourceVersion,
		"zk_host":         zkHost,
		"findings":        findings,
	}, nil
}

// KafkaBridgeExecute adds KRaft controllers alongside ZooKeeper (dual-write bridge mode).
// Command name: "app_upgrade_kafka_bridge"
func KafkaBridgeExecute(params map[string]any) (map[string]any, error) {
	container := str(params, "kafka_container", "kafka")
	kafkaPort := intParam(params, "kafka_port", 9092)
	image     := str(params, "target_version", "7.6.1")
	if image != "" {
		image = fmt.Sprintf("confluentinc/cp-kafka:%s", image)
	} else {
		image = "confluentinc/cp-kafka:7.6.1"
	}

	steps := []string{}

	// 1. Verify broker connectivity (preflight gate)
	if _, err := runCmd("docker", "exec", container, "kafka-broker-api-versions.sh",
		"--bootstrap-server", fmt.Sprintf("localhost:%d", kafkaPort)); err != nil {
		return nil, fmt.Errorf("broker preflight failed: %w", err)
	}
	steps = append(steps, "preflight_verified")

	// 2. Generate cluster ID
	clusterIDOut, err := runCmd("docker", "exec", container, "kafka-storage.sh", "random-uuid")
	if err != nil {
		return nil, fmt.Errorf("generate cluster ID: %w", err)
	}
	clusterID := strings.TrimSpace(clusterIDOut)
	steps = append(steps, "cluster_id_generated")

	// 3. Format KRaft storage (ignore error — may already be formatted)
	runCmd("docker", "exec", container, "kafka-storage.sh", "format",
		"-t", clusterID, "-c", "/etc/kafka/kraft/server.properties")

	// 4. Start KRaft controller container
	kraftCtrl := container + "-kraft-ctrl"
	if _, err := runCmd("docker", "run", "-d",
		"--name", kraftCtrl,
		"--network=host",
		"-e", "KAFKA_PROCESS_ROLES=controller",
		"-e", "KAFKA_NODE_ID=100",
		"-e", "KAFKA_CONTROLLER_QUORUM_VOTERS=100@localhost:9093",
		image, "/etc/confluent/docker/run",
	); err != nil {
		return nil, fmt.Errorf("start kraft controller: %w", err)
	}
	steps = append(steps, "bridge_initiated")

	return map[string]any{
		"status":          "completed",
		"cluster_id":      clusterID,
		"steps_completed": steps,
		"bridge_mode":     "dual_write",
	}, nil
}

// KafkaBridgeSnapshotLocalExecute captures ZooKeeper broker metadata to a local file.
// Command name: "app_snapshot_local_kafka_bridge"
func KafkaBridgeSnapshotLocalExecute(params map[string]any) (map[string]any, error) {
	container := str(params, "kafka_container", "kafka")
	zkHost    := str(params, "kafka_zk_host", "localhost")
	zkPort    := intParam(params, "kafka_zk_port", 2181)
	assetID   := str(params, "asset_id", "default")

	out, err := runCmd("docker", "exec", container, "zookeeper-shell.sh",
		fmt.Sprintf("%s:%d", zkHost, zkPort), "get", "/brokers/ids")
	if err != nil {
		return nil, fmt.Errorf("zk snapshot: %w", err)
	}

	snapPath := fmt.Sprintf("/tmp/nexplane_%s_kafka_zk_snapshot.json", assetID)
	if err := os.WriteFile(snapPath, []byte(out), 0600); err != nil {
		return nil, fmt.Errorf("write snapshot: %w", err)
	}

	return map[string]any{
		"snapshot_path": snapPath,
		"snapshot_type": "kafka_zk_metadata",
	}, nil
}

// KafkaBridgeRestoreLocalExecute stops the KRaft controller and returns to ZK-only mode.
// Command name: "app_restore_local_kafka_bridge"
func KafkaBridgeRestoreLocalExecute(params map[string]any) (map[string]any, error) {
	container := str(params, "kafka_container", "kafka")
	kraftCtrl := container + "-kraft-ctrl"

	runCmd("docker", "stop", kraftCtrl)
	runCmd("docker", "rm", kraftCtrl)

	return map[string]any{
		"status": "restored",
		"mode":   "zookeeper_only",
	}, nil
}
