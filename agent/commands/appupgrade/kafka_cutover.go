// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

package appupgrade

import (
	"fmt"
	"strings"
)

// KafkaCutoverPreflightExecute verifies the bridge phase completed before allowing cutover.
// Command name: "app_preflight_kafka_cutover"
func KafkaCutoverPreflightExecute(params map[string]any) (map[string]any, error) {
	container := str(params, "kafka_container", "kafka")
	kraftCtrl := container + "-kraft-ctrl"

	out, err := runCmd("docker", "inspect", "--format={{.State.Running}}", kraftCtrl)
	if err != nil || strings.TrimSpace(out) != "true" {
		return map[string]any{
			"preflight_passed": false,
			"reason":           "KRaft controller not running — bridge phase must complete first",
		}, nil
	}

	return map[string]any{
		"preflight_passed": true,
		"findings":         []map[string]any{},
	}, nil
}

// KafkaCutoverExecute permanently migrates from ZooKeeper to KRaft.
// This operation is IRREVERSIBLE once committed.
// Command name: "app_upgrade_kafka_cutover"
func KafkaCutoverExecute(params map[string]any) (map[string]any, error) {
	container   := str(params, "kafka_container", "kafka")
	kafkaPort   := intParam(params, "kafka_port", 9092)
	zkContainer := str(params, "kafka_zk_container", "zookeeper")

	steps := []string{}

	// 1. Verify quorum status
	if _, err := runCmd("docker", "exec", container, "kafka-metadata-quorum.sh",
		"--bootstrap-server", fmt.Sprintf("localhost:%d", kafkaPort),
		"describe", "--status"); err != nil {
		return nil, fmt.Errorf("quorum describe: %w", err)
	}
	steps = append(steps, "quorum_verified")

	// 2. Upgrade metadata to KRaft format
	if _, err := runCmd("docker", "exec", container, "kafka-features.sh",
		"--bootstrap-server", fmt.Sprintf("localhost:%d", kafkaPort),
		"upgrade", "--metadata", "3.3"); err != nil {
		return nil, fmt.Errorf("metadata upgrade: %w", err)
	}
	steps = append(steps, "metadata_upgraded")

	// 3. Stop ZooKeeper container
	if _, err := runCmd("docker", "stop", zkContainer); err != nil {
		return nil, fmt.Errorf("stop zookeeper: %w", err)
	}
	steps = append(steps, "zk_stopped")

	return map[string]any{
		"status":            "completed",
		"steps_completed":   steps,
		"kraft_mode":        true,
		"rollback_possible": false,
	}, nil
}

// KafkaCutoverRollbackAttemptExecute always returns rollback_impossible.
// The ZK→KRaft cutover is irreversible once committed.
// Command name: "app_rollback_kafka_cutover"
func KafkaCutoverRollbackAttemptExecute(params map[string]any) (map[string]any, error) {
	return map[string]any{
		"status": "rollback_impossible",
		"reason": "Kafka ZK→KRaft cutover is irreversible once committed. ZooKeeper metadata cannot be recovered from KRaft state.",
	}, nil
}
