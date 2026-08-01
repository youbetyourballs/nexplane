// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

package appupgrade

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"
)

func rmqGet(host string, port int, user, password, path string) (map[string]any, error) {
	url := fmt.Sprintf("http://%s:%d%s", host, port, path)
	req, _ := http.NewRequest("GET", url, nil)
	req.SetBasicAuth(user, password)
	client := &http.Client{Timeout: 15 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("GET %s: %w", path, err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	var result map[string]any
	if err := json.Unmarshal(body, &result); err != nil {
		n := len(body)
		if n > 200 {
			n = 200
		}
		return nil, fmt.Errorf("JSON parse error: %w (body: %s)", err, string(body[:n]))
	}
	return result, nil
}

func rmqPost(host string, port int, user, password, path string, body []byte) error {
	url := fmt.Sprintf("http://%s:%d%s", host, port, path)
	req, _ := http.NewRequest("POST", url, bytes.NewReader(body))
	req.SetBasicAuth(user, password)
	req.Header.Set("Content-Type", "application/json")
	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("POST %s: %w", path, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		b, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("POST %s returned %d: %s", path, resp.StatusCode, string(b))
	}
	return nil
}

// RabbitMQPreflightExecute checks connectivity and version readiness for upgrade.
// Command name: "app_preflight_rabbitmq"
func RabbitMQPreflightExecute(params map[string]any) (map[string]any, error) {
	host          := str(params, "rmq_host", "localhost")
	port          := intParam(params, "rmq_port", 15672)
	user          := str(params, "rmq_user", "guest")
	password      := str(params, "rmq_password", "guest")
	targetVersion := str(params, "target_version", "")

	overview, err := rmqGet(host, port, user, password, "/api/overview")
	if err != nil {
		return map[string]any{
			"preflight_passed": false,
			"findings": []map[string]any{{
				"severity": "CRITICAL",
				"check":    "connectivity",
				"detail":   err.Error(),
			}},
			"target_version": targetVersion,
		}, nil
	}

	detectedVersion, _ := overview["rabbitmq_version"].(string)
	erlangVersion, _   := overview["erlang_version"].(string)

	findings := []map[string]any{}
	if !strings.HasPrefix(detectedVersion, "3.") {
		findings = append(findings, map[string]any{
			"severity": "CRITICAL",
			"check":    "version_path",
			"detail":   fmt.Sprintf("expected 3.x source version, detected %q", detectedVersion),
		})
	}

	hasCritical := len(findings) > 0
	return map[string]any{
		"preflight_passed": !hasCritical,
		"detected_version": detectedVersion,
		"erlang_version":   erlangVersion,
		"findings":         findings,
		"target_version":   targetVersion,
	}, nil
}

// RabbitMQUpgradeExecute performs a Docker-based 3.x → 4.x RabbitMQ upgrade.
// Command name: "app_upgrade_rabbitmq"
func RabbitMQUpgradeExecute(params map[string]any) (map[string]any, error) {
	host          := str(params, "rmq_host", "localhost")
	port          := intParam(params, "rmq_port", 15672)
	user          := str(params, "rmq_user", "guest")
	password      := str(params, "rmq_password", "guest")
	targetVersion := str(params, "target_version", "")
	container     := str(params, "rmq_container", "rabbitmq")

	steps := []string{}

	// 1. Enable feature flags (required before 3.x → 4.x upgrade)
	if out, err := runCmd("docker", "exec", container, "rabbitmqctl", "enable_feature_flag", "all"); err != nil {
		return nil, fmt.Errorf("enable_feature_flags: %s: %w", out, err)
	}
	steps = append(steps, "enable_feature_flags")

	// 2. Stop 3.x container
	runCmd("docker", "stop", container)
	steps = append(steps, "stop_v3")

	// 3. Start 4.x container
	imageTag := fmt.Sprintf("rabbitmq:%s-management", targetVersion)
	if targetVersion == "" {
		imageTag = "rabbitmq:4.0-management"
	}
	newContainer := container + "-v4"
	if out, err := runCmd("docker", "run", "-d",
		"--name", newContainer,
		"-p", "5672:5672",
		"-p", fmt.Sprintf("%d:15672", port),
		"-e", fmt.Sprintf("RABBITMQ_DEFAULT_USER=%s", user),
		"-e", fmt.Sprintf("RABBITMQ_DEFAULT_PASS=%s", password),
		imageTag,
	); err != nil {
		return nil, fmt.Errorf("start_v4: %s: %w", out, err)
	}
	steps = append(steps, "start_v4")

	// 4. Wait up to 60s for management API
	detectedVersion := ""
	ready := false
	for i := 0; i < 12; i++ {
		time.Sleep(5 * time.Second)
		overview, err := rmqGet(host, port, user, password, "/api/overview")
		if err == nil {
			detectedVersion, _ = overview["rabbitmq_version"].(string)
			if strings.HasPrefix(detectedVersion, "4.") {
				ready = true
				break
			}
		}
	}
	if !ready {
		return nil, fmt.Errorf("rabbitmq 4.x did not become ready within 60s")
	}
	steps = append(steps, "verify")

	return map[string]any{
		"status":           "completed",
		"upgraded_version": detectedVersion,
		"steps_completed":  steps,
	}, nil
}

// RabbitMQSnapshotLocalExecute exports RabbitMQ definitions to a local file.
// Command name: "app_snapshot_local_rabbitmq"
func RabbitMQSnapshotLocalExecute(params map[string]any) (map[string]any, error) {
	host     := str(params, "rmq_host", "localhost")
	port     := intParam(params, "rmq_port", 15672)
	user     := str(params, "rmq_user", "guest")
	password := str(params, "rmq_password", "guest")
	assetID  := str(params, "asset_id", "default")

	defs, err := rmqGet(host, port, user, password, "/api/definitions")
	if err != nil {
		return nil, fmt.Errorf("fetch definitions: %w", err)
	}

	data, err := json.Marshal(defs)
	if err != nil {
		return nil, fmt.Errorf("marshal definitions: %w", err)
	}

	snapPath := fmt.Sprintf("/tmp/nexplane_%s_rabbitmq_definitions.json", assetID)
	if err := os.WriteFile(snapPath, data, 0600); err != nil {
		return nil, fmt.Errorf("write snapshot: %w", err)
	}

	return map[string]any{
		"snapshot_path": snapPath,
		"snapshot_type": "rabbitmq_definitions",
	}, nil
}

// RabbitMQRestoreLocalExecute imports RabbitMQ definitions from a local file.
// Command name: "app_restore_local_rabbitmq"
func RabbitMQRestoreLocalExecute(params map[string]any) (map[string]any, error) {
	host         := str(params, "rmq_host", "localhost")
	port         := intParam(params, "rmq_port", 15672)
	user         := str(params, "rmq_user", "guest")
	password     := str(params, "rmq_password", "guest")
	snapPath     := str(params, "snapshot_path", "")

	if snapPath == "" {
		return nil, fmt.Errorf("snapshot_path required for restore")
	}

	data, err := os.ReadFile(snapPath)
	if err != nil {
		return nil, fmt.Errorf("read snapshot %s: %w", snapPath, err)
	}

	if err := rmqPost(host, port, user, password, "/api/definitions", data); err != nil {
		return nil, fmt.Errorf("import definitions: %w", err)
	}

	return map[string]any{
		"status":        "restored",
		"snapshot_path": snapPath,
	}, nil
}
