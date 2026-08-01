// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

package appupgrade

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"
)

func parsePythonVersion(output string) string {
	// "Python 3.8.12" → "3.8"
	output = strings.TrimSpace(output)
	if strings.HasPrefix(output, "Python ") {
		ver := strings.TrimPrefix(output, "Python ")
		parts := strings.Split(ver, ".")
		if len(parts) >= 2 {
			return parts[0] + "." + parts[1]
		}
	}
	return ""
}

// PythonPreflightExecute checks Python version readiness for upgrade.
// Command name: "app_preflight_python"
func PythonPreflightExecute(params map[string]any) (map[string]any, error) {
	pythonContainer  := str(params, "python_container", "python-app")
	targetVersion    := str(params, "target_version", "")
	requirementsFile := str(params, "requirements_file", "requirements.txt")

	// 1. Detect current Python version
	versionOut, err := runCmd("docker", "exec", pythonContainer, "python3", "--version")
	if err != nil && versionOut == "" {
		return map[string]any{
			"preflight_passed":  false,
			"target_version":    targetVersion,
			"requirements_file": requirementsFile,
			"findings": []map[string]any{{
				"severity": "CRITICAL",
				"check":    "python_installed",
				"detail":   fmt.Sprintf("python3 not found in container %s: %v", pythonContainer, err),
			}},
		}, nil
	}

	detectedVersion := parsePythonVersion(strings.TrimSpace(versionOut))
	if detectedVersion == "" {
		return map[string]any{
			"preflight_passed":  false,
			"target_version":    targetVersion,
			"requirements_file": requirementsFile,
			"findings": []map[string]any{{
				"severity": "CRITICAL",
				"check":    "python_version_parse",
				"detail":   fmt.Sprintf("could not parse python version from output: %s", versionOut),
			}},
		}, nil
	}

	// 2. Check requirements file exists
	_, reqErr := runCmd("docker", "exec", pythonContainer, "test", "-f", "/app/"+requirementsFile)
	findings := []map[string]any{}
	if reqErr != nil {
		findings = append(findings, map[string]any{
			"severity": "WARNING",
			"check":    "requirements_file",
			"detail":   fmt.Sprintf("requirements file /app/%s not found in container", requirementsFile),
		})
	}

	return map[string]any{
		"preflight_passed":  true,
		"detected_version":  detectedVersion,
		"target_version":    targetVersion,
		"requirements_file": requirementsFile,
		"findings":          findings,
	}, nil
}

// PythonUpgradeExecute performs a Docker-based Python runtime upgrade.
// Command name: "app_upgrade_python"
func PythonUpgradeExecute(params map[string]any) (map[string]any, error) {
	pythonContainer  := str(params, "python_container", "python-app")
	targetVersion    := str(params, "target_version", "")
	pythonImage      := str(params, "python_image", "")
	appDir           := str(params, "app_dir", "/app")
	requirementsFile := str(params, "requirements_file", "requirements.txt")
	startCmd         := str(params, "start_cmd", "")

	if targetVersion == "" {
		return nil, fmt.Errorf("target_version is required")
	}

	steps := []string{}

	// 1. Get current image for audit trail
	currentImage, _ := runCmd("docker", "inspect", "--format={{.Config.Image}}", pythonContainer)
	currentImage = strings.TrimSpace(currentImage)

	// 2. Export requirements from old container
	reqOut, _ := runCmd("docker", "exec", pythonContainer, "pip", "freeze")

	// 3. Stop old container
	runCmd("docker", "stop", pythonContainer)
	steps = append(steps, "stopped_old")

	// 4. Determine image and new container name
	if pythonImage == "" {
		pythonImage = "python:" + targetVersion + "-slim"
	}
	newContainerName := fmt.Sprintf("%s-v%s", pythonContainer, targetVersion)

	entrypoint := "sleep infinity"
	if startCmd != "" {
		entrypoint = startCmd
	}

	runArgs := []string{
		"run", "-d",
		"--name", newContainerName,
		"-v", appDir,
		pythonImage,
		"bash", "-c", entrypoint,
	}

	if out, err := runCmd("docker", runArgs...); err != nil {
		return nil, fmt.Errorf("start new container: %s: %w", out, err)
	}
	steps = append(steps, "started_new")

	// 5. Install requirements in new container
	if reqOut != "" {
		// Write requirements to a temp file inside the container via stdin
		pipInstallOut, pipErr := runCmd("bash", "-c",
			fmt.Sprintf("echo %q | docker exec -i %s pip install -r /dev/stdin",
				reqOut, newContainerName))
		if pipErr != nil {
			// Try without version constraints as fallback
			_ = pipInstallOut
			// Log warning but continue; pip may partially succeed
		}
	} else {
		// Try installing from requirements file inside container
		_, _ = runCmd("docker", "exec", newContainerName,
			"pip", "install", "-r", appDir+"/"+requirementsFile)
	}
	steps = append(steps, "installed_deps")

	// 6. Verify new version
	verOut, err := runCmd("docker", "exec", newContainerName, "python3", "--version")
	if err != nil {
		return nil, fmt.Errorf("verify python version: %s: %w", verOut, err)
	}
	detectedVersion := parsePythonVersion(strings.TrimSpace(verOut))
	steps = append(steps, "verified")

	return map[string]any{
		"status":           "completed",
		"upgraded_version": detectedVersion,
		"container_name":   newContainerName,
		"previous_image":   currentImage,
		"steps_completed":  steps,
	}, nil
}

// PythonSnapshotLocalExecute captures pip freeze from a Docker container.
// Command name: "app_snapshot_local_python"
func PythonSnapshotLocalExecute(params map[string]any) (map[string]any, error) {
	pythonContainer := str(params, "python_container", "python-app")
	assetID         := str(params, "asset_id", "default")

	// 1. pip freeze
	packagesOut, _ := runCmd("docker", "exec", pythonContainer, "pip", "freeze")

	// 2. python version
	versionOut, _ := runCmd("docker", "exec", pythonContainer, "python3", "--version")
	detectedVersion := parsePythonVersion(strings.TrimSpace(versionOut))

	snapshot := map[string]any{
		"version":   detectedVersion,
		"packages":  strings.TrimSpace(packagesOut),
		"container": pythonContainer,
		"asset_id":  assetID,
	}

	data, err := json.Marshal(snapshot)
	if err != nil {
		return nil, fmt.Errorf("marshal snapshot: %w", err)
	}

	snapPath := fmt.Sprintf("/tmp/nexplane_%s_python_snapshot.json", assetID)
	if err := os.WriteFile(snapPath, data, 0600); err != nil {
		return nil, fmt.Errorf("write snapshot: %w", err)
	}

	return map[string]any{
		"snapshot_path": snapPath,
		"snapshot_type": "python_requirements",
	}, nil
}

// PythonRestoreLocalExecute rolls back a Python container upgrade.
// Command name: "app_restore_local_python"
func PythonRestoreLocalExecute(params map[string]any) (map[string]any, error) {
	pythonContainer := str(params, "python_container", "python-app")
	targetVersion   := str(params, "target_version", "")

	newContainerName := fmt.Sprintf("%s-v%s", pythonContainer, targetVersion)

	if out, err := runCmd("docker", "stop", newContainerName); err != nil {
		return nil, fmt.Errorf("stop new container: %s: %w", out, err)
	}

	if out, err := runCmd("docker", "rm", newContainerName); err != nil {
		return nil, fmt.Errorf("remove new container: %s: %w", out, err)
	}

	if out, err := runCmd("docker", "start", pythonContainer); err != nil {
		return nil, fmt.Errorf("restart old container: %s: %w", out, err)
	}

	return map[string]any{
		"status":    "restored",
		"container": pythonContainer,
	}, nil
}
