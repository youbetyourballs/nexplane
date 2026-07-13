// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

package migration

import (
	"crypto/md5"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
)

// DiscoverApplicationProfileExecute maps the host's running workload to the
// profile schema expected by the capture_behavioral_baseline and
// verify_against_baseline executors.
func DiscoverApplicationProfileExecute(params map[string]any) (map[string]any, error) {
	hostname, _ := os.Hostname()

	endpoints := discoverEndpoints()
	dependencies := discoverDependencies()
	configFiles := discoverConfigFiles()
	services := discoverRunningServices()
	libraryVersions := discoverLibraryVersions()
	processes := discoverProcesses()

	profile := map[string]any{
		"processes":        processes,
		"endpoints":        endpoints,
		"dependencies":     dependencies,
		"config_files":     configFiles,
		"services":         services,
		"library_versions": libraryVersions,
	}

	return map[string]any{
		"action":   "discover_application_profile",
		"hostname": hostname,
		"profile":  profile,
	}, nil
}

func discoverEndpoints() []map[string]any {
	out, err := exec.Command("ss", "-tulpn").Output()
	if err != nil {
		out, _ = exec.Command("netstat", "-tulpn").Output()
	}

	var endpoints []map[string]any
	seen := map[string]bool{}
	re := regexp.MustCompile(`(?:tcp|udp)\s+\S+\s+\S+\s+[*\d.:]+:(\d+)\s+`)

	for _, line := range strings.Split(string(out), "\n") {
		m := re.FindStringSubmatch(line)
		if m == nil {
			continue
		}
		port := m[1]
		if port == "22" || seen[port] {
			continue
		}
		seen[port] = true

		proc := extractProcessFromSSLine(line)
		endpoints = append(endpoints, map[string]any{
			"protocol":             "tcp",
			"port":                 parseInt(port),
			"process":              proc,
			"declared_health_path": "/health",
			"source":               "runtime",
		})
	}
	return endpoints
}

func discoverDependencies() []map[string]any {
	var deps []map[string]any

	// Runtime: outbound established connections
	out, _ := exec.Command("ss", "-tnp", "state", "established").Output()
	connSeen := map[string]bool{}
	reConn := regexp.MustCompile(`(\d+\.\d+\.\d+\.\d+):(\d+)\s+.*users:\(\("([^"]+)"`)
	for _, line := range strings.Split(string(out), "\n") {
		m := reConn.FindStringSubmatch(line)
		if m == nil {
			continue
		}
		key := m[1] + ":" + m[2]
		if connSeen[key] {
			continue
		}
		connSeen[key] = true
		depType := "http"
		if m[2] == "5432" || m[2] == "3306" || m[2] == "27017" {
			depType = "db"
		}
		deps = append(deps, map[string]any{
			"type":         depType,
			"host":         m[1],
			"port":         parseInt(m[2]),
			"dsn_template": "",
			"source":       "runtime",
			"confidence":   "runtime_only",
		})
	}

	// Static: scan config files for DSNs
	for _, cf := range discoverConfigFiles() {
		path, _ := cf["path"].(string)
		content, err := os.ReadFile(path)
		if err != nil {
			continue
		}
		dsnRe := regexp.MustCompile(`(?:postgresql|postgres|mysql|mongodb)://[^\s"']+`)
		for _, dsn := range dsnRe.FindAllString(string(content), -1) {
			host, port := parseDSN(dsn)
			key := host + ":" + port
			confidence := "config_only"
			if connSeen[key] {
				confidence = "both"
			}
			deps = append(deps, map[string]any{
				"type":         "db",
				"host":         host,
				"port":         parseInt(port),
				"dsn_template": dsn,
				"source":       "config",
				"confidence":   confidence,
			})
		}
	}
	return deps
}

func discoverConfigFiles() []map[string]any {
	searchRoots := []string{"/etc", "/opt", "/usr/local/etc", "/var"}
	exts := map[string]string{
		".ini": "ini", ".conf": "conf", ".yml": "yaml", ".yaml": "yaml",
		".env": "env", ".properties": "properties", ".toml": "toml",
	}
	var files []map[string]any
	seen := map[string]bool{}

	for _, root := range searchRoots {
		_ = filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
			if err != nil || info == nil || info.IsDir() || seen[path] {
				return nil
			}
			if info.Size() > 1<<20 { // skip >1MB
				return nil
			}
			ext := strings.ToLower(filepath.Ext(path))
			format, ok := exts[ext]
			if !ok {
				return nil
			}
			seen[path] = true
			files = append(files, map[string]any{
				"path":           path,
				"format":         format,
				"extracted_keys": extractConfigKeys(path),
			})
			return nil
		})
	}
	return files
}

func discoverRunningServices() []map[string]any {
	out, err := exec.Command("systemctl", "list-units", "--type=service",
		"--state=running", "--no-legend", "--plain").Output()
	if err != nil {
		return nil
	}
	var services []map[string]any
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		fields := strings.Fields(line)
		if len(fields) < 1 {
			continue
		}
		services = append(services, map[string]any{
			"name":  strings.TrimSuffix(fields[0], ".service"),
			"state": "active",
		})
	}
	return services
}

func discoverLibraryVersions() []map[string]any {
	libs := []struct{ name, cmd string }{
		{"libpq", "dpkg-query -W -f=${Version} libpq5 2>/dev/null || rpm -q --queryformat '%{VERSION}' libpq 2>/dev/null"},
		{"openssl", "openssl version 2>/dev/null | awk '{print $2}'"},
		{"python3", "python3 --version 2>/dev/null | awk '{print $2}'"},
		{"java", "java -version 2>&1 | head -1 | awk -F'\"' '{print $2}'"},
	}
	var result []map[string]any
	for _, lib := range libs {
		out, err := exec.Command("sh", "-c", lib.cmd).Output()
		if err != nil || strings.TrimSpace(string(out)) == "" {
			continue
		}
		result = append(result, map[string]any{
			"name":    lib.name,
			"version": strings.TrimSpace(string(out)),
			"path":    "",
		})
	}
	return result
}

func discoverProcesses() []map[string]any {
	out, _ := exec.Command("ps", "auxf").Output()
	var procs []map[string]any
	for _, line := range strings.Split(string(out), "\n") {
		fields := strings.Fields(line)
		if len(fields) < 11 {
			continue
		}
		if fields[0] == "USER" {
			continue
		}
		procs = append(procs, map[string]any{
			"name":       filepath.Base(fields[10]),
			"pid":        fields[1],
			"executable": fields[10],
		})
	}
	return procs
}

// --- helpers ---

func extractProcessFromSSLine(line string) string {
	re := regexp.MustCompile(`users:\(\("([^"]+)"`)
	m := re.FindStringSubmatch(line)
	if m != nil {
		return m[1]
	}
	return ""
}

func extractConfigKeys(path string) []string {
	content, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	re := regexp.MustCompile(`(?m)^([A-Za-z_][A-Za-z0-9_]*)\s*[=:]`)
	var keys []string
	seen := map[string]bool{}
	for _, m := range re.FindAllStringSubmatch(string(content), 50) {
		k := m[1]
		if !seen[k] {
			keys = append(keys, k)
			seen[k] = true
		}
	}
	return keys
}

func parseDSN(dsn string) (host, port string) {
	re := regexp.MustCompile(`@([^:/]+):(\d+)`)
	m := re.FindStringSubmatch(dsn)
	if m != nil {
		return m[1], m[2]
	}
	// localhost default ports
	if strings.HasPrefix(dsn, "postgresql://") || strings.HasPrefix(dsn, "postgres://") {
		return "localhost", "5432"
	}
	if strings.HasPrefix(dsn, "mysql://") {
		return "localhost", "3306"
	}
	return "localhost", "0"
}

func parseInt(s string) int {
	var n int
	fmt.Sscanf(s, "%d", &n)
	return n
}

func fileHash(path string) string {
	b, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	return fmt.Sprintf("%x", md5.Sum(b))[:8]
}
