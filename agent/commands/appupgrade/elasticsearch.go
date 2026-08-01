// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

package appupgrade

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os/exec"
	"strings"
	"time"
)

func str(params map[string]any, key, def string) string {
	if v, ok := params[key]; ok {
		if s, ok := v.(string); ok && s != "" {
			return s
		}
	}
	return def
}

func intParam(params map[string]any, key string, def int) int {
	if v, ok := params[key]; ok {
		switch n := v.(type) {
		case float64:
			return int(n)
		case int:
			return n
		}
	}
	return def
}

func esGet(host string, port int, scheme, user, password, path string) (map[string]any, error) {
	url := fmt.Sprintf("%s://%s:%d%s", scheme, host, port, path)
	req, _ := http.NewRequest("GET", url, nil)
	if user != "" {
		req.SetBasicAuth(user, password)
	}
	client := &http.Client{Timeout: 10 * time.Second}
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

func esPut(host string, port int, scheme, user, password, path, body string) error {
	url := fmt.Sprintf("%s://%s:%d%s", scheme, host, port, path)
	req, _ := http.NewRequest("PUT", url, strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	if user != "" {
		req.SetBasicAuth(user, password)
	}
	client := &http.Client{Timeout: 10 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("PUT %s: %w", path, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		b, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("PUT %s returned %d: %s", path, resp.StatusCode, string(b))
	}
	return nil
}

func runCmd(name string, args ...string) (string, error) {
	out, err := exec.Command(name, args...).CombinedOutput()
	return string(out), err
}

// ElasticsearchPreflightExecute checks connectivity, version, cluster health, and Java.
// Command name: "app_preflight_elasticsearch"
func ElasticsearchPreflightExecute(params map[string]any) (map[string]any, error) {
	host          := str(params, "es_host", "localhost")
	port          := intParam(params, "es_port", 9200)
	scheme        := str(params, "es_scheme", "http")
	user          := str(params, "es_user", "")
	pass          := str(params, "es_password", "")
	targetVersion := str(params, "target_version", "")

	root, err := esGet(host, port, scheme, user, pass, "/")
	if err != nil {
		return map[string]any{
			"status":   "preflight_blocked",
			"reason":   fmt.Sprintf("Elasticsearch not reachable at %s://%s:%d: %s", scheme, host, port, err),
			"findings": []map[string]any{{"severity": "CRITICAL", "check": "connectivity", "detail": err.Error()}},
		}, nil
	}

	version := ""
	if v, ok := root["version"].(map[string]any); ok {
		version, _ = v["number"].(string)
	}

	findings := []map[string]any{}

	if strings.HasPrefix(targetVersion, "8") && !strings.HasPrefix(version, "7") {
		findings = append(findings, map[string]any{
			"severity": "CRITICAL",
			"check":    "version_path",
			"detail":   fmt.Sprintf("source version %s must be 7.17.x before upgrading to 8.x", version),
		})
	}

	health, err := esGet(host, port, scheme, user, pass, "/_cluster/health")
	if err == nil {
		status, _ := health["status"].(string)
		if status == "red" {
			findings = append(findings, map[string]any{
				"severity": "CRITICAL",
				"check":    "cluster_health",
				"detail":   "cluster health is RED -- resolve all shard issues before upgrading",
			})
		}
	}

	javaOut, _ := runCmd("java", "-version")
	if !strings.Contains(javaOut, "17") && !strings.Contains(javaOut, "21") {
		findings = append(findings, map[string]any{
			"severity": "WARNING",
			"check":    "java_version",
			"detail":   fmt.Sprintf("Java 17+ recommended for ES 8.x; detected: %s", strings.TrimSpace(javaOut)),
		})
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

// ElasticsearchUpgradeExecute performs the rolling upgrade sequence.
// Command name: "app_upgrade_elasticsearch"
func ElasticsearchUpgradeExecute(params map[string]any) (map[string]any, error) {
	host          := str(params, "es_host", "localhost")
	port          := intParam(params, "es_port", 9200)
	scheme        := str(params, "es_scheme", "http")
	user          := str(params, "es_user", "")
	pass          := str(params, "es_password", "")
	targetVersion := str(params, "target_version", "")

	steps := []string{}

	if err := esPut(host, port, scheme, user, pass,
		"/_cluster/settings",
		`{"persistent":{"cluster.routing.allocation.enable":"primaries"}}`); err != nil {
		return nil, fmt.Errorf("disable_shard_allocation: %w", err)
	}
	steps = append(steps, "disable_shard_allocation")

	if _, err := runCmd("curl", "-s", "-X", "POST",
		fmt.Sprintf("%s://%s:%d/_flush", scheme, host, port)); err != nil {
		return nil, fmt.Errorf("flush: %w", err)
	}
	steps = append(steps, "flush")

	if _, err := runCmd("systemctl", "stop", "elasticsearch"); err != nil {
		if _, err2 := runCmd("docker", "stop", "es7"); err2 != nil {
			return nil, fmt.Errorf("stop_elasticsearch: systemctl: %v; docker: %v", err, err2)
		}
	}
	steps = append(steps, "stop_elasticsearch")

	pkgName := fmt.Sprintf("elasticsearch=%s", targetVersion)
	if out, err := runCmd("apt-get", "install", "-y", "--allow-downgrades", pkgName); err != nil {
		pkgNameYum := fmt.Sprintf("elasticsearch-%s", targetVersion)
		if out2, err2 := runCmd("yum", "install", "-y", pkgNameYum); err2 != nil {
			if out3, err3 := runCmd("docker", "run", "-d", "--name", "es8",
				"-p", fmt.Sprintf("%d:9200", port+1),
				"-e", "discovery.type=single-node",
				"-e", "xpack.security.enabled=false",
				fmt.Sprintf("elasticsearch:%s", targetVersion)); err3 != nil {
				return nil, fmt.Errorf("upgrade_package: apt: %s; yum: %s; docker: %s", out, out2, out3)
			}
		}
	}
	steps = append(steps, "upgrade_package")

	runCmd("systemctl", "start", "elasticsearch")
	steps = append(steps, "start_elasticsearch")

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
		return nil, fmt.Errorf("elasticsearch did not become healthy within 120s after upgrade")
	}
	steps = append(steps, "wait_for_green")

	esPut(host, port, scheme, user, pass, "/_cluster/settings",
		`{"persistent":{"cluster.routing.allocation.enable":null}}`)
	esPut(host, port+1, scheme, user, pass, "/_cluster/settings",
		`{"persistent":{"cluster.routing.allocation.enable":null}}`)
	steps = append(steps, "enable_shard_allocation")

	return map[string]any{
		"steps_completed": steps,
		"target_version":  targetVersion,
	}, nil
}

// ElasticsearchSnapshotLocalExecute tars the ES data directory to a local path.
// Command name: "app_snapshot_local_elasticsearch"
func ElasticsearchSnapshotLocalExecute(params map[string]any) (map[string]any, error) {
	localPath := str(params, "local_path", "/tmp/es_snap.tar.gz")
	dataDir   := str(params, "es_data_dir", "/var/lib/elasticsearch")

	out, err := runCmd("tar", "-czf", localPath, dataDir)
	if err != nil {
		return nil, fmt.Errorf("tar snapshot: %s: %w", out, err)
	}
	return map[string]any{"local_path": localPath, "data_dir": dataDir}, nil
}

// ElasticsearchRestoreLocalExecute restores from a local tar snapshot.
// Command name: "app_restore_local_elasticsearch"
func ElasticsearchRestoreLocalExecute(params map[string]any) (map[string]any, error) {
	localPath := str(params, "local_path", "")
	dataDir   := str(params, "es_data_dir", "/var/lib/elasticsearch")

	if localPath == "" {
		return nil, fmt.Errorf("local_path required for restore")
	}
	runCmd("systemctl", "stop", "elasticsearch")
	if out, err := runCmd("rm", "-rf", dataDir); err != nil {
		return nil, fmt.Errorf("rm data dir: %s: %w", out, err)
	}
	if out, err := runCmd("tar", "-xzf", localPath, "-C", "/"); err != nil {
		return nil, fmt.Errorf("tar restore: %s: %w", out, err)
	}
	runCmd("systemctl", "start", "elasticsearch")
	return map[string]any{"restored": true, "local_path": localPath}, nil
}
