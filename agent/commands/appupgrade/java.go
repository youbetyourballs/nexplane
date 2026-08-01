// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

package appupgrade

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"
)

func parseJavaVersion(output string) string {
	for _, line := range strings.Split(output, "\n") {
		if strings.Contains(line, "version") {
			start := strings.Index(line, `"`)
			end := strings.LastIndex(line, `"`)
			if start != -1 && end > start {
				ver := line[start+1 : end]
				// Handle 1.8.x format (Java 8)
				if strings.HasPrefix(ver, "1.") {
					parts := strings.Split(ver, ".")
					if len(parts) >= 2 {
						return parts[1]
					}
				}
				// Handle 11+ format: 11, 17.0.1, 21.0.2
				parts := strings.Split(ver, ".")
				return parts[0]
			}
		}
	}
	return ""
}

// JavaPreflightExecute checks Java version readiness for upgrade.
// Command name: "app_preflight_java"
func JavaPreflightExecute(params map[string]any) (map[string]any, error) {
	targetVersion    := str(params, "target_version", "")
	serviceCheckCmd  := str(params, "service_check_cmd", "")

	out, err := runCmd("bash", "-c", "java -version 2>&1")
	if err != nil && out == "" {
		return map[string]any{
			"preflight_passed": false,
			"target_version":   targetVersion,
			"findings": []map[string]any{{
				"severity": "CRITICAL",
				"check":    "java_installed",
				"detail":   fmt.Sprintf("java not found: %v", err),
			}},
		}, nil
	}

	detectedVersion := parseJavaVersion(out)
	if detectedVersion == "" {
		return map[string]any{
			"preflight_passed": false,
			"target_version":   targetVersion,
			"findings": []map[string]any{{
				"severity": "CRITICAL",
				"check":    "java_version_parse",
				"detail":   fmt.Sprintf("could not parse java version from output: %s", out),
			}},
		}, nil
	}

	dependentServices := []string{}
	if serviceCheckCmd != "" {
		svcOut, _ := runCmd("bash", "-c", serviceCheckCmd)
		for _, line := range strings.Split(strings.TrimSpace(svcOut), "\n") {
			if line != "" {
				dependentServices = append(dependentServices, line)
			}
		}
	}

	findings := []map[string]any{}
	if detectedVersion == targetVersion {
		findings = append(findings, map[string]any{
			"severity": "WARNING",
			"check":    "version_already_current",
			"detail":   fmt.Sprintf("detected version %q already matches target %q", detectedVersion, targetVersion),
		})
	}

	return map[string]any{
		"preflight_passed":   true,
		"detected_version":   detectedVersion,
		"target_version":     targetVersion,
		"dependent_services": dependentServices,
		"findings":           findings,
	}, nil
}

// JavaUpgradeExecute performs a Docker-based Java runtime upgrade.
// Command name: "app_upgrade_java"
func JavaUpgradeExecute(params map[string]any) (map[string]any, error) {
	javaContainer := str(params, "java_container", "java-app")
	targetVersion  := str(params, "target_version", "")
	javaImage      := str(params, "java_image", "")
	appJar         := str(params, "app_jar", "")
	appArgs        := str(params, "app_args", "")
	javaHomeNew    := str(params, "java_home_new", "/usr/lib/jvm/java-17")

	if targetVersion == "" {
		return nil, fmt.Errorf("target_version is required")
	}

	steps := []string{}

	// 1. Get current image for audit trail in return value
	currentImage, _ := runCmd("docker", "inspect", "--format={{.Config.Image}}", javaContainer)
	currentImage = strings.TrimSpace(currentImage)

	// 2. Stop old container
	runCmd("docker", "stop", javaContainer)
	steps = append(steps, "stopped_old")

	// 3. Determine image and new container name
	if javaImage == "" {
		javaImage = "eclipse-temurin:" + targetVersion + "-jre"
	}
	newContainerName := fmt.Sprintf("%s-v%s", javaContainer, targetVersion)

	runArgs := []string{"run", "-d", "--name", newContainerName, "-e", "JAVA_HOME=" + javaHomeNew, javaImage}
	if appJar != "" {
		javaCmd := "java"
		if appArgs != "" {
			javaCmd += " " + appArgs
		}
		javaCmd += " -jar " + appJar
		runArgs = append(runArgs, "bash", "-c", javaCmd)
	} else {
		runArgs = append(runArgs, "sleep", "infinity")
	}

	if out, err := runCmd("docker", runArgs...); err != nil {
		return nil, fmt.Errorf("start new container: %s: %w", out, err)
	}
	steps = append(steps, "started_new")

	// 4. Verify new version
	verOut, err := runCmd("docker", "exec", newContainerName, "bash", "-c", "java -version 2>&1")
	if err != nil {
		return nil, fmt.Errorf("verify java version: %s: %w", verOut, err)
	}
	detectedVersion := parseJavaVersion(verOut)
	steps = append(steps, "verified")

	return map[string]any{
		"status":           "completed",
		"upgraded_version": detectedVersion,
		"container_name":   newContainerName,
		"previous_image":   currentImage,
		"steps_completed":  steps,
	}, nil
}

// JavaSnapshotLocalExecute captures JVM config from a Docker container.
// Command name: "app_snapshot_local_java"
func JavaSnapshotLocalExecute(params map[string]any) (map[string]any, error) {
	javaContainer := str(params, "java_container", "java-app")
	assetID        := str(params, "asset_id", "default")

	versionOut, _ := runCmd("docker", "exec", javaContainer, "bash", "-c", "java -version 2>&1")
	psOut, _       := runCmd("docker", "exec", javaContainer, "bash", "-c", "ps aux | grep java || echo NO_JAVA_PROCESS")

	snapshot := map[string]any{
		"java_version_output": versionOut,
		"java_processes":      psOut,
		"container":           javaContainer,
		"asset_id":            assetID,
	}

	data, err := json.Marshal(snapshot)
	if err != nil {
		return nil, fmt.Errorf("marshal snapshot: %w", err)
	}

	snapPath := fmt.Sprintf("/tmp/nexplane_%s_java_snapshot.json", assetID)
	if err := os.WriteFile(snapPath, data, 0600); err != nil {
		return nil, fmt.Errorf("write snapshot: %w", err)
	}

	return map[string]any{
		"snapshot_path": snapPath,
		"snapshot_type": "java_config",
	}, nil
}

// JavaRestoreLocalExecute rolls back a Java container upgrade.
// Command name: "app_restore_local_java"
func JavaRestoreLocalExecute(params map[string]any) (map[string]any, error) {
	javaContainer := str(params, "java_container", "java-app")
	targetVersion  := str(params, "target_version", "")

	newContainerName := fmt.Sprintf("%s-v%s", javaContainer, targetVersion)

	if out, err := runCmd("docker", "stop", newContainerName); err != nil {
		return nil, fmt.Errorf("stop new container: %s: %w", out, err)
	}

	if out, err := runCmd("docker", "rm", newContainerName); err != nil {
		return nil, fmt.Errorf("remove new container: %s: %w", out, err)
	}

	if out, err := runCmd("docker", "start", javaContainer); err != nil {
		return nil, fmt.Errorf("restart old container: %s: %w", out, err)
	}

	return map[string]any{
		"status":    "restored",
		"container": javaContainer,
	}, nil
}
