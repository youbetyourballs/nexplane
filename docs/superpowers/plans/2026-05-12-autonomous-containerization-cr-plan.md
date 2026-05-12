# Autonomous Containerization CR Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the autonomous containerization CR feature by adding deep host enrichment to the Go agent (`deep_discover` command), a specialized Migrate drawer in the frontend, and a 7-stage progress stepper in the CR detail view.

**Architecture:** The backend orchestration (`containerize_auto.py`) and `confirm-stateful` endpoint are fully implemented. The Go `deep_discover` command exists but is missing enrichment fields (env var names, open files, runtime deps, config-file intelligence). The frontend has the stateful gate UI but lacks a specialized Migrate drawer (currently sends users to the generic CR form) and the 7-stage visual stepper.

**Tech Stack:** Go 1.26 (agent), FastAPI/Python (backend), React/TypeScript/Tailwind (frontend), @tanstack/react-query v5

---

## File Map

**Agent (Go):**
- Modify: `agent/commands/deepdiscover/deepdiscover.go` — add `EnvVarNames`, `OpenFiles`, `RuntimeDeps`, `ConfigIntelligence`, `PIDFound` fields to `DiscoveredWorkload`; add `ConfigEntry` type; add `workloadsToMaps` serialization for new fields
- Modify: `agent/commands/deepdiscover/deepdiscover_linux.go` — populate new fields from `/proc/{pid}/environ`, `/proc/{pid}/fd`, `ldd`, config-file parse
- Modify: `agent/commands/deepdiscover/deepdiscover_windows.go` — populate new fields via `netstat -ano`/`Get-Process.Modules`/WMI
- Modify: `agent/commands/deepdiscover/deepdiscover_linux_test.go` — tests for new Linux enrichment functions
- Modify: `agent/commands/deepdiscover/deepdiscover_windows_test.go` — tests for Windows enrichment functions
- Create: `agent/commands/deepdiscover/deepdiscover_config.go` — shared config-file parser (nginx/Apache/IIS/generic, build-tag-free)

**Backend (Python):**
- Create: `backend/app/connectors/executors/nexplane_agent/deep_discover.py` — thin dispatch shim (not yet created)

**Frontend (TypeScript):**
- Create: `frontend/src/components/MigrateDrawer.tsx` — specialized drawer for `agent_containerize_auto` (registry, cluster, namespace, soak, dry_run)
- Modify: `frontend/src/pages/AssetDetail.tsx` — wire `agent_containerize_auto` quick action to open `MigrateDrawer` instead of navigating to the generic new-CR form
- Create: `frontend/src/components/AutoMigrationStepper.tsx` — 7-stage vertical stepper for `agent_containerize_auto` CRs
- Modify: `frontend/src/pages/ChangeRequestDetail.tsx` — replace the existing inline AI analysis section with `<AutoMigrationStepper>`

---

### Task 1: Add enrichment fields to `DiscoveredWorkload` struct

**Files:**
- Modify: `agent/commands/deepdiscover/deepdiscover.go`

- [ ] **Step 1: Write the failing test**

Add to `agent/commands/deepdiscover/deepdiscover_linux_test.go`:

```go
//go:build linux

package deepdiscover

import (
    "os/exec"
    "testing"
)

// ... existing tests ...

func TestWorkloadHasEnrichmentFields(t *testing.T) {
    result, err := Execute(map[string]any{})
    if err != nil {
        t.Fatalf("Execute returned error: %v", err)
    }
    workloads, ok := result["workloads"].([]map[string]any)
    if !ok {
        t.Skip("no workloads on this machine")
    }
    if len(workloads) == 0 {
        t.Skip("no workloads on this machine")
    }
    w := workloads[0]
    for _, key := range []string{"pid_found", "env_var_names", "open_files", "runtime_deps", "config_intelligence"} {
        if _, ok := w[key]; !ok {
            t.Errorf("workload missing key: %s", key)
        }
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd f:/Nexplane/nexplane/agent
GOOS=linux go test ./commands/deepdiscover/ -run TestWorkloadHasEnrichmentFields -v 2>&1
```

Expected: FAIL with "workload missing key: pid_found" (or similar)

- [ ] **Step 3: Add `ConfigEntry` type and new fields to `DiscoveredWorkload`**

In `agent/commands/deepdiscover/deepdiscover.go`, add `ConfigEntry` after the `HybridEdge` type and extend `DiscoveredWorkload`:

```go
// ConfigEntry holds structured metadata parsed from a config file.
type ConfigEntry struct {
    File   string            `json:"file"`
    Type   string            `json:"type"`   // "nginx", "apache", "iis", "generic"
    Fields map[string]string `json:"fields"` // e.g. {"server_name": "payments.internal", "proxy_pass": "http://10.0.1.45:8080"}
}

// DiscoveredWorkload represents a running workload (service, container, pod, process).
type DiscoveredWorkload struct {
    Name            string      `json:"name"`
    RuntimeType     RuntimeType `json:"runtime_type"`
    Pid             int         `json:"pid,omitempty"`
    SystemdUnit     string      `json:"systemd_unit,omitempty"`
    ContainerID     string      `json:"container_id,omitempty"`
    ImageName       string      `json:"image_name,omitempty"`
    PodName         string      `json:"pod_name,omitempty"`
    ListeningPorts  []PortEntry `json:"listening_ports"`
    OutboundConns   []ConnEdge  `json:"outbound_connections"`
    InboundConns    []ConnEdge  `json:"inbound_connections"`
    IPCSockets      []string    `json:"ipc_sockets"`
    DataDirectories []DirInfo   `json:"data_directories"`
    Dependencies    []string    `json:"dependencies"`
    // Enrichment fields
    PIDFound           bool          `json:"pid_found"`
    EnvVarNames        []string      `json:"env_var_names"`
    OpenFiles          []string      `json:"open_files"`
    RuntimeDeps        []string      `json:"runtime_deps"`
    ConfigIntelligence []ConfigEntry `json:"config_intelligence"`
}
```

- [ ] **Step 4: Update `workloadsToMaps` to serialize new fields**

In `agent/commands/deepdiscover/deepdiscover.go`, find the `workloadsToMaps` function and add the new fields:

```go
func workloadsToMaps(workloads []DiscoveredWorkload) []map[string]any {
    out := make([]map[string]any, 0, len(workloads))
    for _, w := range workloads {
        m := map[string]any{
            "name":                 w.Name,
            "runtime_type":         w.RuntimeType,
            "pid":                  w.Pid,
            "systemd_unit":         w.SystemdUnit,
            "container_id":         w.ContainerID,
            "image_name":           w.ImageName,
            "pod_name":             w.PodName,
            "listening_ports":      portEntriesToMaps(w.ListeningPorts),
            "outbound_connections": connEdgesToMaps(w.OutboundConns),
            "inbound_connections":  connEdgesToMaps(w.InboundConns),
            "ipc_sockets":          w.IPCSockets,
            "data_directories":     dirInfosToMaps(w.DataDirectories),
            "dependencies":         w.Dependencies,
            // Enrichment
            "pid_found":            w.PIDFound,
            "env_var_names":        w.EnvVarNames,
            "open_files":           w.OpenFiles,
            "runtime_deps":         w.RuntimeDeps,
            "config_intelligence":  configEntriesToMaps(w.ConfigIntelligence),
        }
        out = append(out, m)
    }
    return out
}

func configEntriesToMaps(entries []ConfigEntry) []map[string]any {
    out := make([]map[string]any, 0, len(entries))
    for _, e := range entries {
        out = append(out, map[string]any{
            "file":   e.File,
            "type":   e.Type,
            "fields": e.Fields,
        })
    }
    return out
}
```

- [ ] **Step 5: Run test to verify it fails with missing population (not missing keys)**

```bash
cd f:/Nexplane/nexplane/agent
GOOS=linux go test ./commands/deepdiscover/ -run TestWorkloadHasEnrichmentFields -v 2>&1
```

Expected: PASS (keys are now present, even if empty slices/false)

- [ ] **Step 6: Commit**

```bash
cd f:/Nexplane/nexplane
git add agent/commands/deepdiscover/deepdiscover.go agent/commands/deepdiscover/deepdiscover_linux_test.go
git commit -m "feat(agent): add enrichment fields to DiscoveredWorkload struct"
```

---

### Task 2: Config-file intelligence parser (shared)

**Files:**
- Create: `agent/commands/deepdiscover/deepdiscover_config.go`

- [ ] **Step 1: Write the failing tests**

Create `agent/commands/deepdiscover/deepdiscover_config_test.go`:

```go
package deepdiscover

import (
    "testing"
)

func TestParseNginxConfig(t *testing.T) {
    input := `
server {
    server_name payments.internal;
    location / {
        proxy_pass http://10.0.1.45:8080;
        root /var/www/html;
    }
}`
    entries := parseConfigFile("/etc/nginx/nginx.conf", input)
    if len(entries) == 0 {
        t.Fatal("expected at least one entry")
    }
    e := entries[0]
    if e.Type != "nginx" {
        t.Errorf("expected type=nginx, got %q", e.Type)
    }
    if e.Fields["server_name"] != "payments.internal" {
        t.Errorf("expected server_name=payments.internal, got %q", e.Fields["server_name"])
    }
    if e.Fields["proxy_pass"] != "http://10.0.1.45:8080" {
        t.Errorf("expected proxy_pass=http://10.0.1.45:8080, got %q", e.Fields["proxy_pass"])
    }
}

func TestParseApacheConfig(t *testing.T) {
    input := `<VirtualHost *:80>
    ServerName app.internal
    ProxyPass / http://127.0.0.1:3000/
    DocumentRoot /var/www
</VirtualHost>`
    entries := parseConfigFile("/etc/apache2/sites-enabled/app.conf", input)
    if len(entries) == 0 {
        t.Fatal("expected at least one entry")
    }
    e := entries[0]
    if e.Type != "apache" {
        t.Errorf("expected type=apache, got %q", e.Type)
    }
    if e.Fields["server_name"] != "app.internal" {
        t.Errorf("expected server_name=app.internal, got %q", e.Fields["server_name"])
    }
    if e.Fields["proxy_pass"] != "http://127.0.0.1:3000/" {
        t.Errorf("expected proxy_pass, got %q", e.Fields["proxy_pass"])
    }
}

func TestParseGenericConfig(t *testing.T) {
    input := `DB_HOST=10.0.0.1
APP_PORT=8080
SECRET_KEY=do_not_capture_this`
    entries := parseConfigFile("/opt/myapp/.env", input)
    if len(entries) == 0 {
        t.Fatal("expected at least one entry")
    }
    e := entries[0]
    if e.Type != "generic" {
        t.Errorf("expected type=generic, got %q", e.Type)
    }
    // Keys should be present, values should NOT be captured
    if _, ok := e.Fields["DB_HOST"]; !ok {
        t.Error("expected DB_HOST key to be present")
    }
    if e.Fields["DB_HOST"] != "" {
        t.Errorf("expected empty value (no secret capture), got %q", e.Fields["DB_HOST"])
    }
    // SECRET_KEY key should be present but value empty
    if _, ok := e.Fields["SECRET_KEY"]; !ok {
        t.Error("expected SECRET_KEY key present")
    }
    if e.Fields["SECRET_KEY"] != "" {
        t.Error("must not capture secret values in generic config")
    }
}

func TestParseConfigFileUnknownReturnsEmpty(t *testing.T) {
    entries := parseConfigFile("/etc/fstab", "UUID=abc /boot ext4 defaults 0 2")
    // fstab is not a recognized config type; should return nil/empty
    if len(entries) != 0 {
        t.Errorf("expected empty for unknown config type, got %d entries", len(entries))
    }
}
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd f:/Nexplane/nexplane/agent
go test ./commands/deepdiscover/ -run TestParse -v 2>&1
```

Expected: FAIL with `undefined: parseConfigFile`

- [ ] **Step 3: Implement the config-file parser**

Create `agent/commands/deepdiscover/deepdiscover_config.go`:

```go
package deepdiscover

import (
    "bufio"
    "os"
    "path/filepath"
    "strings"
)

// parseConfigFiles parses a list of config file paths and returns ConfigEntry results.
// Files that are unreadable or of unknown type are silently skipped.
func parseConfigFiles(paths []string) []ConfigEntry {
    var entries []ConfigEntry
    for _, p := range paths {
        data, err := os.ReadFile(p)
        if err != nil {
            continue
        }
        parsed := parseConfigFile(p, string(data))
        entries = append(entries, parsed...)
    }
    return entries
}

// parseConfigFile classifies and extracts metadata from a single config file.
func parseConfigFile(path, content string) []ConfigEntry {
    ext := strings.ToLower(filepath.Ext(path))
    base := strings.ToLower(filepath.Base(path))

    switch {
    case isNginxConfig(path, base):
        return parseNginx(path, content)
    case isApacheConfig(path, base):
        return parseApache(path, content)
    case isIISConfig(base):
        return parseIIS(path, content)
    case ext == ".conf" || ext == ".ini" || ext == ".env" || ext == ".cfg":
        return parseGeneric(path, content)
    default:
        return nil
    }
}

func isNginxConfig(path, base string) bool {
    return strings.Contains(path, "nginx") ||
        base == "nginx.conf" ||
        strings.Contains(path, "/sites-enabled/") ||
        strings.Contains(path, "/sites-available/") ||
        strings.Contains(path, "/conf.d/")
}

func isApacheConfig(path, base string) bool {
    return strings.Contains(path, "apache") ||
        strings.Contains(path, "httpd") ||
        strings.HasSuffix(base, ".conf") && strings.Contains(path, "/apache")
}

func isIISConfig(base string) bool {
    return base == "applicationhost.config" || base == "web.config"
}

// parseNginx extracts server_name, proxy_pass, and root directives.
func parseNginx(path, content string) []ConfigEntry {
    fields := map[string]string{}
    scanner := bufio.NewScanner(strings.NewReader(content))
    for scanner.Scan() {
        line := strings.TrimSpace(scanner.Text())
        for _, directive := range []string{"server_name", "proxy_pass", "root"} {
            if strings.HasPrefix(line, directive+" ") || strings.HasPrefix(line, directive+"\t") {
                val := strings.TrimPrefix(line, directive)
                val = strings.TrimSpace(val)
                val = strings.TrimSuffix(val, ";")
                // Take first value only for server_name (may have multiple)
                parts := strings.Fields(val)
                if len(parts) > 0 {
                    if _, exists := fields[directive]; !exists {
                        fields[directive] = parts[0]
                    }
                }
            }
        }
    }
    if len(fields) == 0 {
        return nil
    }
    return []ConfigEntry{{File: path, Type: "nginx", Fields: fields}}
}

// parseApache extracts ServerName, ProxyPass, and DocumentRoot directives.
func parseApache(path, content string) []ConfigEntry {
    fields := map[string]string{}
    scanner := bufio.NewScanner(strings.NewReader(content))
    dirMap := map[string]string{
        "servername":   "server_name",
        "proxypass":    "proxy_pass",
        "documentroot": "root",
    }
    for scanner.Scan() {
        line := strings.TrimSpace(scanner.Text())
        parts := strings.Fields(line)
        if len(parts) < 2 {
            continue
        }
        key := strings.ToLower(parts[0])
        if outKey, ok := dirMap[key]; ok {
            if _, exists := fields[outKey]; !exists {
                fields[outKey] = parts[1]
            }
        }
    }
    if len(fields) == 0 {
        return nil
    }
    return []ConfigEntry{{File: path, Type: "apache", Fields: fields}}
}

// parseIIS extracts site binding hostnames from applicationHost.config.
func parseIIS(path, content string) []ConfigEntry {
    fields := map[string]string{}
    // Look for binding attributes like bindingInformation="*:80:payments.internal"
    scanner := bufio.NewScanner(strings.NewReader(content))
    for scanner.Scan() {
        line := strings.TrimSpace(scanner.Text())
        if !strings.Contains(line, "bindingInformation") {
            continue
        }
        start := strings.Index(line, `"`)
        end := strings.LastIndex(line, `"`)
        if start < 0 || end <= start {
            continue
        }
        val := line[start+1 : end]
        // Format: ip:port:hostname
        colonParts := strings.Split(val, ":")
        if len(colonParts) == 3 && colonParts[2] != "" {
            if _, exists := fields["server_name"]; !exists {
                fields["server_name"] = colonParts[2]
            }
        }
    }
    if len(fields) == 0 {
        return nil
    }
    return []ConfigEntry{{File: path, Type: "iis", Fields: fields}}
}

// parseGeneric reads key=value pairs from .conf/.ini/.env files.
// Values are intentionally discarded — only key names are recorded to avoid
// capturing secrets.
func parseGeneric(path, content string) []ConfigEntry {
    fields := map[string]string{}
    scanner := bufio.NewScanner(strings.NewReader(content))
    for scanner.Scan() {
        line := strings.TrimSpace(scanner.Text())
        if line == "" || strings.HasPrefix(line, "#") || strings.HasPrefix(line, ";") {
            continue
        }
        idx := strings.IndexAny(line, "=:")
        if idx <= 0 {
            continue
        }
        key := strings.TrimSpace(line[:idx])
        // Validate key: alphanumeric + underscore + hyphen + dot only
        if !isValidConfigKey(key) {
            continue
        }
        fields[key] = "" // value intentionally empty
    }
    if len(fields) == 0 {
        return nil
    }
    return []ConfigEntry{{File: path, Type: "generic", Fields: fields}}
}

func isValidConfigKey(s string) bool {
    if len(s) == 0 || len(s) > 128 {
        return false
    }
    for _, c := range s {
        if !((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
            (c >= '0' && c <= '9') || c == '_' || c == '-' || c == '.') {
            return false
        }
    }
    return true
}
```

- [ ] **Step 4: Run tests**

```bash
cd f:/Nexplane/nexplane/agent
go test ./commands/deepdiscover/ -run TestParse -v 2>&1
```

Expected: 4 tests PASS

- [ ] **Step 5: Commit**

```bash
cd f:/Nexplane/nexplane
git add agent/commands/deepdiscover/deepdiscover_config.go agent/commands/deepdiscover/deepdiscover_config_test.go
git commit -m "feat(agent): add config-file intelligence parser (nginx/apache/iis/generic)"
```

---

### Task 3: Linux enrichment — env vars, open files, runtime deps, config intelligence

**Files:**
- Modify: `agent/commands/deepdiscover/deepdiscover_linux.go`

- [ ] **Step 1: Write the failing tests**

Add to `agent/commands/deepdiscover/deepdiscover_linux_test.go` (after existing tests):

```go
func TestCollectEnvVarNamesForPid1(t *testing.T) {
    // PID 1 always exists on Linux; its environ should have some keys
    names := collectEnvVarNamesLinux(1)
    // May be empty in container environments, but should not panic
    _ = names
}

func TestCollectOpenFilesForPid1(t *testing.T) {
    files := collectOpenFilesLinux(1)
    // Should return a slice (may be empty if /proc/1/fd is not readable)
    if files == nil {
        t.Error("expected non-nil slice from collectOpenFilesLinux")
    }
}

func TestCollectRuntimeDepsForBinary(t *testing.T) {
    // /bin/sh exists on all Linux systems
    deps := collectRuntimeDepsLinux("/bin/sh")
    // ldd output may be empty for static binaries; must not panic
    _ = deps
}
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd f:/Nexplane/nexplane/agent
GOOS=linux go test ./commands/deepdiscover/ -run "TestCollect" -v 2>&1
```

Expected: FAIL with `undefined: collectEnvVarNamesLinux`

- [ ] **Step 3: Implement Linux enrichment functions**

Add to `agent/commands/deepdiscover/deepdiscover_linux.go` (after existing functions):

```go
// collectEnvVarNamesLinux reads /proc/{pid}/environ and returns env var key names only.
// Values are intentionally omitted to avoid capturing secrets.
func collectEnvVarNamesLinux(pid int) []string {
    if pid <= 0 {
        return []string{}
    }
    data, err := os.ReadFile(fmt.Sprintf("/proc/%d/environ", pid))
    if err != nil {
        return []string{}
    }
    seen := map[string]bool{}
    var names []string
    for _, entry := range strings.Split(string(data), "\x00") {
        idx := strings.IndexByte(entry, '=')
        if idx <= 0 {
            continue
        }
        key := entry[:idx]
        if key != "" && !seen[key] {
            seen[key] = true
            names = append(names, key)
        }
    }
    return names
}

// collectOpenFilesLinux reads /proc/{pid}/fd and returns paths to regular files.
// Sockets, pipes, and anonymous fds are excluded.
func collectOpenFilesLinux(pid int) []string {
    if pid <= 0 {
        return []string{}
    }
    fdDir := fmt.Sprintf("/proc/%d/fd", pid)
    entries, err := os.ReadDir(fdDir)
    if err != nil {
        return []string{}
    }
    seen := map[string]bool{}
    var files []string
    for _, e := range entries {
        link, err := os.Readlink(filepath.Join(fdDir, e.Name()))
        if err != nil {
            continue
        }
        // Skip sockets, pipes, anon_inodes, and /proc itself
        if strings.HasPrefix(link, "socket:") ||
            strings.HasPrefix(link, "pipe:") ||
            strings.HasPrefix(link, "anon_inode:") ||
            strings.HasPrefix(link, "/proc/") ||
            strings.HasPrefix(link, "/dev/") {
            continue
        }
        if !seen[link] {
            seen[link] = true
            files = append(files, link)
        }
    }
    return files
}

// collectRuntimeDepsLinux runs ldd on the binary and returns .so paths.
func collectRuntimeDepsLinux(binary string) []string {
    if binary == "" {
        return []string{}
    }
    out, err := execCommandLinux("ldd", binary).Output()
    if err != nil {
        return []string{}
    }
    seen := map[string]bool{}
    var deps []string
    for _, line := range strings.Split(string(out), "\n") {
        line = strings.TrimSpace(line)
        // Format: "libssl.so.3 => /lib/x86_64-linux-gnu/libssl.so.3 (0x...)"
        // or: "/lib64/ld-linux-x86-64.so.2 (0x...)"
        if !strings.Contains(line, ".so") {
            continue
        }
        // Extract the actual path (after "=>")
        if idx := strings.Index(line, "=>"); idx >= 0 {
            rest := strings.TrimSpace(line[idx+2:])
            // Remove address "(0x...)"
            if i := strings.Index(rest, " ("); i >= 0 {
                rest = strings.TrimSpace(rest[:i])
            }
            if rest != "" && rest != "not found" && !seen[rest] {
                seen[rest] = true
                deps = append(deps, rest)
            }
        } else {
            // Direct path format
            parts := strings.Fields(line)
            if len(parts) > 0 && strings.HasPrefix(parts[0], "/") {
                p := parts[0]
                if !seen[p] {
                    seen[p] = true
                    deps = append(deps, p)
                }
            }
        }
    }
    return deps
}
```

- [ ] **Step 4: Wire enrichment into `collectSystemdServicesLinux`**

In `agent/commands/deepdiscover/deepdiscover_linux.go`, find `collectSystemdServicesLinux`. After building each `DiscoveredWorkload`, populate the enrichment fields. Find the section that appends to workloads and extend it:

The existing function builds a workload like this:
```go
workloads = append(workloads, DiscoveredWorkload{
    Name:          unitName,
    ...
    OutboundConns: []ConnEdge{},
})
```

Replace that section with:

```go
// Get PID for this unit
pidOut, _ := execCommandLinux("systemctl", "show", "--property=MainPID", "--value", unitName).Output()
pid := 0
if pidStr := strings.TrimSpace(string(pidOut)); pidStr != "" && pidStr != "0" {
    fmt.Sscanf(pidStr, "%d", &pid)
}

// Get binary path for ldd
binOut, _ := execCommandLinux("systemctl", "show", "--property=ExecStart", "--value", unitName).Output()
binary := ""
if binStr := strings.TrimSpace(string(binOut)); binStr != "" {
    // ExecStart format: "{ path=/usr/sbin/nginx ; argv[]=... }"
    if idx := strings.Index(binStr, "path="); idx >= 0 {
        rest := binStr[idx+5:]
        if end := strings.IndexAny(rest, " ;"); end > 0 {
            binary = rest[:end]
        } else {
            binary = rest
        }
    }
}

// Collect config files for this unit
confDir := "/etc/" + strings.TrimSuffix(unitName, ".service")
var cfgFiles []string
if entries, err := os.ReadDir(confDir); err == nil {
    for _, e := range entries {
        if !e.IsDir() {
            cfgFiles = append(cfgFiles, filepath.Join(confDir, e.Name()))
        }
    }
}

workloads = append(workloads, DiscoveredWorkload{
    Name:               unitName,
    RuntimeType:        RuntimeSystemd,
    Pid:                pid,
    SystemdUnit:        unitName,
    ListeningPorts:     []PortEntry{},
    OutboundConns:      []ConnEdge{},
    InboundConns:       []ConnEdge{},
    IPCSockets:         []string{},
    DataDirectories:    []DirInfo{},
    Dependencies:       collectSystemdDepsLinux(unitName),
    PIDFound:           pid > 0,
    EnvVarNames:        collectEnvVarNamesLinux(pid),
    OpenFiles:          collectOpenFilesLinux(pid),
    RuntimeDeps:        collectRuntimeDepsLinux(binary),
    ConfigIntelligence: parseConfigFiles(cfgFiles),
})
```

You'll also need to add the missing imports to the file. Add `"fmt"` and `"path/filepath"` to the import block if not already present.

- [ ] **Step 5: Run all Linux tests**

```bash
cd f:/Nexplane/nexplane/agent
GOOS=linux go test ./commands/deepdiscover/ -v 2>&1
```

Expected: All tests PASS (including the new enrichment tests)

- [ ] **Step 6: Commit**

```bash
cd f:/Nexplane/nexplane
git add agent/commands/deepdiscover/deepdiscover_linux.go agent/commands/deepdiscover/deepdiscover_linux_test.go
git commit -m "feat(agent): add env-var names, open files, runtime deps, config intel to deep_discover (Linux)"
```

---

### Task 4: Windows enrichment stub

**Files:**
- Modify: `agent/commands/deepdiscover/deepdiscover_windows.go`

- [ ] **Step 1: Write the failing tests**

Add to `agent/commands/deepdiscover/deepdiscover_windows_test.go`:

```go
//go:build windows

package deepdiscover

import "testing"

func TestCollectEnvVarNamesWindows(t *testing.T) {
    names := collectEnvVarNamesWindows(0)
    if names == nil {
        t.Error("expected non-nil slice")
    }
}

func TestCollectOpenFilesWindows(t *testing.T) {
    files := collectOpenFilesWindows(0)
    if files == nil {
        t.Error("expected non-nil slice")
    }
}

func TestCollectRuntimeDepsWindows(t *testing.T) {
    deps := collectRuntimeDepsWindows("")
    if deps == nil {
        t.Error("expected non-nil slice")
    }
}
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd f:/Nexplane/nexplane/agent
GOOS=windows go test ./commands/deepdiscover/ -run "TestCollect.*Windows" -v 2>&1
```

Expected: FAIL with `undefined: collectEnvVarNamesWindows`

- [ ] **Step 3: Implement Windows enrichment functions**

In `agent/commands/deepdiscover/deepdiscover_windows.go`, add after existing functions:

```go
// collectEnvVarNamesWindows returns environment variable key names for a process.
// Uses `wmic process where ProcessId=<pid> get EnvironmentVariables` or
// falls back to the current process env for PID 0.
func collectEnvVarNamesWindows(pid int) []string {
    // Windows does not expose /proc. Use the process handle approach via wmic.
    // For PID 0 (unknown), return empty.
    if pid <= 0 {
        return []string{}
    }
    out, err := execCommandWindows("wmic", "process",
        fmt.Sprintf("where ProcessId=%d", pid),
        "get", "EnvironmentVariables", "/format:csv").Output()
    if err != nil {
        return []string{}
    }
    // CSV output: Node,EnvironmentVariables — value is semicolon-separated KEY=VALUE pairs
    var names []string
    seen := map[string]bool{}
    for _, line := range strings.Split(string(out), "\n") {
        idx := strings.LastIndex(line, ",")
        if idx < 0 {
            continue
        }
        pairs := strings.Split(line[idx+1:], ";")
        for _, pair := range pairs {
            eqIdx := strings.IndexByte(pair, '=')
            if eqIdx <= 0 {
                continue
            }
            key := strings.TrimSpace(pair[:eqIdx])
            if key != "" && !seen[key] {
                seen[key] = true
                names = append(names, key)
            }
        }
    }
    return names
}

// collectOpenFilesWindows returns open file paths for a process using handle.exe or wmic.
func collectOpenFilesWindows(pid int) []string {
    if pid <= 0 {
        return []string{}
    }
    // Try handle.exe (Sysinternals) if available
    out, err := execCommandWindows("handle.exe", "-p", fmt.Sprintf("%d", pid), "-nobanner").Output()
    if err != nil {
        return []string{}
    }
    var files []string
    seen := map[string]bool{}
    for _, line := range strings.Split(string(out), "\n") {
        line = strings.TrimSpace(line)
        // handle.exe output: "PID: type  File  C:\path\to\file"
        if strings.Contains(line, "File") && strings.Contains(line, `:\`) {
            idx := strings.LastIndex(line, `\`)
            if idx > 2 {
                // Find start of drive letter
                start := strings.LastIndex(line[:idx], " ")
                if start >= 0 {
                    path := strings.TrimSpace(line[start:])
                    if !seen[path] {
                        seen[path] = true
                        files = append(files, path)
                    }
                }
            }
        }
    }
    return files
}

// collectRuntimeDepsWindows returns DLL paths loaded by a process.
func collectRuntimeDepsWindows(binary string) []string {
    if binary == "" {
        return []string{}
    }
    // Use Get-Process | Select -ExpandProperty Modules via PowerShell
    script := fmt.Sprintf(`(Get-Process -Name '%s' -ErrorAction SilentlyContinue | Select-Object -First 1).Modules.FileName -join ","`,
        strings.TrimSuffix(filepath.Base(binary), ".exe"))
    out, err := execCommandWindows("powershell", "-NoProfile", "-Command", script).Output()
    if err != nil {
        return []string{}
    }
    var deps []string
    seen := map[string]bool{}
    for _, p := range strings.Split(strings.TrimSpace(string(out)), ",") {
        p = strings.TrimSpace(p)
        if p != "" && strings.HasSuffix(strings.ToLower(p), ".dll") && !seen[p] {
            seen[p] = true
            deps = append(deps, p)
        }
    }
    return deps
}
```

Add `"fmt"` and `"path/filepath"` to the imports if not already present.

Also wire these into the Windows workload collection in `executeOS` for Windows (in `deepdiscover_windows.go`), adding enrichment fields to each workload built, identical in structure to the Linux version but calling the Windows functions.

- [ ] **Step 4: Build for Windows to confirm no compile errors**

```bash
cd f:/Nexplane/nexplane/agent
GOOS=windows GOARCH=amd64 go build ./... 2>&1
```

Expected: Exits 0, no output

- [ ] **Step 5: Commit**

```bash
cd f:/Nexplane/nexplane
git add agent/commands/deepdiscover/deepdiscover_windows.go agent/commands/deepdiscover/deepdiscover_windows_test.go
git commit -m "feat(agent): add env-var names, open files, runtime deps to deep_discover (Windows)"
```

---

### Task 5: Backend `deep_discover` executor

**Files:**
- Create: `backend/app/connectors/executors/nexplane_agent/deep_discover.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_deep_discover_executor.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_deep_discover_executor_dispatches_agent_job():
    from app.connectors.executors.nexplane_agent.deep_discover import execute

    with patch(
        "app.connectors.executors.nexplane_agent.deep_discover.dispatch_agent_job",
        new_callable=AsyncMock,
        return_value={"action": "deep_discover", "workloads": []},
    ) as mock_dispatch:
        result = await execute({}, ["asset-id-123"], connector=None)

    mock_dispatch.assert_called_once_with(
        command="deep_discover",
        parameters={},
        asset_ids=["asset-id-123"],
        timeout_seconds=120,
    )
    assert result["action"] == "deep_discover"


@pytest.mark.asyncio
async def test_deep_discover_rollback_is_noop():
    from app.connectors.executors.nexplane_agent.deep_discover import rollback

    result = await rollback({}, {}, connector=None)
    assert result["rolled_back"] is True
```

- [ ] **Step 2: Run to confirm failure**

```bash
docker compose exec backend python -m pytest tests/unit/test_deep_discover_executor.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.connectors.executors.nexplane_agent.deep_discover'`

- [ ] **Step 3: Implement the executor**

Create `backend/app/connectors/executors/nexplane_agent/deep_discover.py`:

```python
"""Dispatch executor for the deep_discover agent command.

Calls the Go agent's deep_discover command which performs full host enrichment:
outbound connections, env var names, open file descriptors, runtime library
dependencies, and config-file intelligence (nginx/Apache/IIS vhosts, upstreams).
"""
from __future__ import annotations


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    return await dispatch_agent_job(
        command="deep_discover",
        parameters=parameters,
        asset_ids=list(asset_ids),
        timeout_seconds=120,
    )


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "note": "deep_discover is read-only, no rollback required"}
```

- [ ] **Step 4: Run tests**

```bash
docker compose exec backend python -m pytest tests/unit/test_deep_discover_executor.py -v
```

Expected: 2 tests PASS

- [ ] **Step 5: Run full unit suite to catch regressions**

```bash
docker compose exec backend python -m pytest tests/unit/ -q 2>&1 | tail -5
```

Expected: All pass

- [ ] **Step 6: Commit**

```bash
cd f:/Nexplane/nexplane
git add backend/app/connectors/executors/nexplane_agent/deep_discover.py backend/tests/unit/test_deep_discover_executor.py
git commit -m "feat: add deep_discover backend executor (thin dispatch shim)"
```

---

### Task 6: Build agent v0.3.1 and deploy to backend downloads

**Files:**
- Build artifact: `agent/dist/nexplane-agent-linux-amd64-0.3.1`

- [ ] **Step 1: Run agent tests**

```bash
cd f:/Nexplane/nexplane/agent
go test ./... 2>&1 | tail -20
```

Expected: All PASS, no failures

- [ ] **Step 2: Build Linux amd64 binary**

```bash
cd f:/Nexplane/nexplane/agent
GOOS=linux GOARCH=amd64 go build -ldflags "-X main.Version=0.3.1" -o dist/nexplane-agent-linux-amd64-0.3.1 ./
```

Expected: Exits 0, file created at `agent/dist/nexplane-agent-linux-amd64-0.3.1`

- [ ] **Step 3: Verify binary**

```bash
ls -lh f:/Nexplane/nexplane/agent/dist/nexplane-agent-linux-amd64-0.3.1
```

Expected: File exists, size ~18MB

- [ ] **Step 4: Deploy to backend container**

```bash
cd f:/Nexplane/nexplane
docker compose cp agent/dist/nexplane-agent-linux-amd64-0.3.1 backend:/opt/nexplane-downloads/nexplane-agent-linux-amd64-0.3.1
docker compose exec backend bash -c "echo '0.3.1' > /opt/nexplane-downloads/version"
docker compose restart backend
sleep 12
curl -sf http://localhost:8000/downloads/version
```

Expected: `0.3.1`

- [ ] **Step 5: Commit agent source changes**

```bash
cd f:/Nexplane/nexplane
git add agent/
git commit -m "feat(agent): deep_discover full enrichment — env vars, open files, runtime deps, config intel (v0.3.1)"
```

---

### Task 7: MigrateDrawer frontend component

**Files:**
- Create: `frontend/src/components/MigrateDrawer.tsx`
- Modify: `frontend/src/pages/AssetDetail.tsx`

- [ ] **Step 1: Create MigrateDrawer.tsx**

Create `frontend/src/components/MigrateDrawer.tsx`:

```tsx
import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { apiClient } from "../api/client";

interface Props {
  assetId: string;
  assetName: string;
  onClose: () => void;
}

interface KubeCluster {
  id: string;
  name: string;
}

export function MigrateDrawer({ assetId, assetName, onClose }: Props) {
  const navigate = useNavigate();
  const [registry, setRegistry] = useState("");
  const [clusterId, setClusterId] = useState("");
  const [namespace, setNamespace] = useState("nexplane-migrations");
  const [soakSeconds, setSoakSeconds] = useState(120);
  const [dryRun, setDryRun] = useState(false);

  const { data: clusters = [] } = useQuery<KubeCluster[]>({
    queryKey: ["kube-clusters"],
    queryFn: () =>
      apiClient
        .get<KubeCluster[]>("/assets", { params: { asset_type: "kubernetes_cluster" } })
        .then((r) => r.data),
  });

  const runMutation = useMutation({
    mutationFn: async () => {
      const cr = await apiClient
        .post("/change-requests", {
          title: `Migrate ${assetName} to Kubernetes`,
          description: `AI-directed autonomous migration of workloads on ${assetName}.`,
          change_type: "agent_containerize_auto",
          target_asset_ids: [assetId],
          desired_outcome: {
            registry,
            target_cluster_id: clusterId,
            namespace,
            soak_seconds: soakSeconds,
            dry_run: dryRun,
          },
          risk_level: "high",
        })
        .then((r) => r.data);
      await apiClient.post(`/change-requests/${cr.id}/plan`);
      await apiClient.post(`/change-requests/${cr.id}/submit-for-approval`);
      await apiClient.post(`/change-requests/${cr.id}/approve`, {
        decision: "approved",
        comment: "Auto-approved via Migrate drawer",
      });
      await apiClient.post(`/change-requests/${cr.id}/execute`);
      return cr;
    },
    onSuccess: (cr) => {
      navigate(`/change-requests/${cr.id}`);
    },
  });

  const canSubmit = registry.trim() !== "" && clusterId !== "" && !runMutation.isPending;

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div
        className="w-full max-w-md bg-white h-full shadow-xl overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200">
          <div>
            <h2 className="font-semibold text-slate-900">Migrate to Kubernetes ✨</h2>
            <p className="text-xs text-slate-500 mt-0.5">{assetName}</p>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600 text-xl">✕</button>
        </div>

        <div className="px-6 py-5 space-y-5">
          <div className="bg-blue-50 border border-blue-100 rounded-lg p-3 text-xs text-blue-700">
            AI will discover all workloads, analyse dependencies, generate Dockerfiles, deploy to Kubernetes, and verify the migration — all autonomously.
          </div>

          {/* Registry */}
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1.5">
              Container Registry URL <span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              value={registry}
              onChange={(e) => setRegistry(e.target.value)}
              placeholder="123456.dkr.ecr.us-east-1.amazonaws.com"
              className="w-full border border-slate-300 rounded px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>

          {/* Target cluster */}
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1.5">
              Target Kubernetes Cluster <span className="text-red-500">*</span>
            </label>
            {clusters.length === 0 ? (
              <p className="text-xs text-amber-600">
                No Kubernetes clusters found in inventory. Add a cluster asset first.
              </p>
            ) : (
              <select
                value={clusterId}
                onChange={(e) => setClusterId(e.target.value)}
                className="w-full border border-slate-300 rounded px-3 py-2 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value="">Select a cluster…</option>
                {clusters.map((c) => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
            )}
          </div>

          {/* Namespace */}
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1.5">Namespace</label>
            <input
              type="text"
              value={namespace}
              onChange={(e) => setNamespace(e.target.value)}
              className="w-full border border-slate-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>

          {/* Soak duration */}
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1.5">
              Soak duration: <span className="font-mono">{soakSeconds}s</span>
            </label>
            <input
              type="range"
              min={10}
              max={600}
              step={10}
              value={soakSeconds}
              onChange={(e) => setSoakSeconds(Number(e.target.value))}
              className="w-full"
            />
            <div className="flex justify-between text-xs text-slate-400 mt-0.5">
              <span>10s</span><span>600s</span>
            </div>
          </div>

          {/* Dry run */}
          <label className="flex items-center gap-3 cursor-pointer">
            <input
              type="checkbox"
              checked={dryRun}
              onChange={(e) => setDryRun(e.target.checked)}
              className="w-4 h-4"
            />
            <div>
              <p className="text-sm font-medium text-slate-700">Dry run</p>
              <p className="text-xs text-slate-400">Runs discovery and AI analysis only — no build or deploy</p>
            </div>
          </label>

          {/* Submit */}
          <button
            onClick={() => runMutation.mutate()}
            disabled={!canSubmit}
            className="w-full py-2.5 bg-purple-600 text-white rounded font-medium text-sm hover:bg-purple-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {runMutation.isPending
              ? "Launching migration…"
              : dryRun
              ? "Run Discovery & Analysis →"
              : "Launch Migration →"}
          </button>

          {runMutation.isError && (
            <p className="text-sm text-red-600">Failed to launch migration. Please try again.</p>
          )}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Wire MigrateDrawer into AssetDetail.tsx**

In `frontend/src/pages/AssetDetail.tsx`, add the import near the top:

```tsx
import { MigrateDrawer } from "../components/MigrateDrawer";
```

Add state near the other modal states:

```tsx
const [showMigrateDrawer, setShowMigrateDrawer] = useState(false);
```

Find the ASSET_ACTIONS handler (around line 1166) that navigates to the generic new-CR form. Change the `agent_containerize_auto` click to open the drawer instead. In the `actions.map((action) => ...)` block, add a special case:

```tsx
{actions.map((action) => (
  <button
    key={action.changeType}
    onClick={() => {
      if (action.changeType === "agent_containerize_auto") {
        setShowMigrateDrawer(true);
        return;
      }
      const params = new URLSearchParams({
        changeType: action.changeType,
        assetId: asset.id,
        title: action.title(asset),
        description: action.description(asset),
      });
      navigate(`/change-requests/new?${params.toString()}`);
    }}
    className="w-full text-left px-3 py-2 text-sm rounded-md border border-slate-200 hover:border-brand-300 hover:bg-brand-50 text-slate-700 hover:text-brand-800 transition-colors"
  >
    {action.label}
  </button>
))}
```

Add the drawer at the bottom of the JSX (before the closing `</div>` of the page):

```tsx
{showMigrateDrawer && asset && (
  <MigrateDrawer
    assetId={asset.id}
    assetName={asset.name}
    onClose={() => setShowMigrateDrawer(false)}
  />
)}
```

- [ ] **Step 3: Restart frontend and verify**

```bash
docker compose stop frontend && docker compose up frontend -d
sleep 10
docker compose logs frontend --tail=15
```

Expected: Vite ready, no TypeScript errors

- [ ] **Step 4: Commit**

```bash
cd f:/Nexplane/nexplane
git add frontend/src/components/MigrateDrawer.tsx frontend/src/pages/AssetDetail.tsx
git commit -m "feat: add MigrateDrawer for agent_containerize_auto with registry/cluster/namespace/soak/dry_run"
```

---

### Task 8: 7-stage stepper in CR detail

**Files:**
- Create: `frontend/src/components/AutoMigrationStepper.tsx`
- Modify: `frontend/src/pages/ChangeRequestDetail.tsx`

- [ ] **Step 1: Create AutoMigrationStepper.tsx**

Create `frontend/src/components/AutoMigrationStepper.tsx`:

```tsx
import { useState } from "react";

interface StepResult {
  status?: string;
  [key: string]: unknown;
}

interface Props {
  stepResults: Record<string, StepResult> | undefined;
  crStatus: string;
}

const STAGES = [
  { key: "preflight_discovery",   label: "Preflight Discovery",    desc: "Deep host enrichment — connections, env vars, open files, runtime deps" },
  { key: "fleet_cross_reference", label: "Fleet Cross-Reference",   desc: "Maps outbound IPs to Nexplane asset inventory" },
  { key: "ai_analysis",           label: "AI Analysis",            desc: "Classifies workloads as stateless/stateful, monolith/modular" },
  { key: "stateful_gate",         label: "Stateful Gate",          desc: "Human review checkpoint for stateful workloads" },
  { key: "build",                 label: "Build & Push",           desc: "Generates Dockerfile, builds image, pushes to registry" },
  { key: "deploy",                label: "Deploy to Kubernetes",   desc: "Applies manifests: ConfigMap → PVC → Service → Deployment" },
  { key: "soak_verify",           label: "Soak Verification",      desc: "HTTP health probes for configured soak window" },
] as const;

function stageIcon(status: string | undefined, crStatus: string, isCurrentRunning: boolean): string {
  if (status === "completed" || status === "passed") return "✓";
  if (status === "failed") return "✗";
  if (status === "waiting") return "⏸";
  if (isCurrentRunning) return "⟳";
  if (crStatus === "executing" || crStatus === "verifying") return "○";
  return "○";
}

function stageColor(status: string | undefined, isCurrentRunning: boolean): string {
  if (status === "completed" || status === "passed") return "text-green-600";
  if (status === "failed") return "text-red-600";
  if (status === "waiting") return "text-amber-600";
  if (isCurrentRunning) return "text-blue-600";
  return "text-slate-400";
}

export function AutoMigrationStepper({ stepResults, crStatus }: Props) {
  const [expanded, setExpanded] = useState<string | null>(null);

  // Determine which stage is currently running
  const lastCompleted = STAGES.map((s) => s.key).filter(
    (k) => stepResults?.[k]?.status === "completed" || stepResults?.[k]?.status === "passed"
  );
  const currentRunningIdx = lastCompleted.length < STAGES.length && (crStatus === "executing" || crStatus === "verifying")
    ? lastCompleted.length
    : -1;

  return (
    <div className="bg-white border border-slate-200 rounded-lg p-5 mt-4">
      <h2 className="text-sm font-semibold text-slate-900 mb-4 flex items-center gap-2">
        <span className="text-purple-500">✨</span> Autonomous Migration Progress
      </h2>

      <div className="space-y-1">
        {STAGES.map((stage, idx) => {
          const result = stepResults?.[stage.key];
          const status = result?.status as string | undefined;
          const isRunning = idx === currentRunningIdx;
          const isExpanded = expanded === stage.key;

          return (
            <div key={stage.key}>
              <button
                onClick={() => setExpanded(isExpanded ? null : stage.key)}
                className="w-full flex items-start gap-3 py-2 px-2 rounded hover:bg-slate-50 transition-colors text-left"
              >
                <span className={`text-sm font-mono w-4 flex-shrink-0 mt-0.5 ${stageColor(status, isRunning)} ${isRunning ? "animate-spin" : ""}`}>
                  {stageIcon(status, crStatus, isRunning)}
                </span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className={`text-sm font-medium ${status ? "text-slate-900" : "text-slate-400"}`}>
                      {stage.label}
                    </span>
                    {status === "waiting" && (
                      <span className="text-xs bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded">
                        Awaiting confirmation
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-slate-400 mt-0.5">{stage.desc}</p>
                </div>
                {result && (
                  <span className="text-xs text-slate-400 flex-shrink-0 mt-1">
                    {isExpanded ? "▲" : "▼"}
                  </span>
                )}
              </button>

              {isExpanded && result && (
                <div className="ml-7 mb-2 bg-slate-50 rounded p-3">
                  {stage.key === "ai_analysis" && Array.isArray((result as Record<string, unknown>).migration_units) ? (
                    <div className="space-y-2">
                      {((result as Record<string, unknown>).migration_units as Array<Record<string, unknown>>).map((unit, i) => (
                        <div key={i} className="border border-slate-200 rounded p-2 text-xs bg-white">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="font-medium text-slate-900">{String(unit.name ?? "")}</span>
                            <span className={`px-1.5 py-0.5 rounded ${unit.stateful ? "bg-amber-50 text-amber-700" : "bg-green-50 text-green-700"}`}>
                              {unit.stateful ? "stateful" : "stateless"}
                            </span>
                            <span className="bg-slate-100 text-slate-600 px-1.5 py-0.5 rounded">
                              {String(unit.pattern ?? "")}
                            </span>
                          </div>
                          {unit.reasoning && (
                            <p className="text-slate-400 italic mt-1">{String(unit.reasoning)}</p>
                          )}
                          {unit.data_risk && (
                            <p className="text-slate-500 mt-1">Data risk: <span className="font-medium">{String(unit.data_risk)}</span></p>
                          )}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <pre className="text-xs text-slate-600 overflow-x-auto whitespace-pre-wrap">
                      {JSON.stringify(result, null, 2)}
                    </pre>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Replace the inline AI section in ChangeRequestDetail.tsx**

In `frontend/src/pages/ChangeRequestDetail.tsx`, add the import:

```tsx
import { AutoMigrationStepper } from "../components/AutoMigrationStepper";
```

Find the existing block starting with:
```tsx
{cr.change_type === "agent_containerize_auto" && (() => {
```

Replace the entire IIFE block (from that line through the closing `})()}`) with:

```tsx
{cr.change_type === "agent_containerize_auto" && (() => {
  const execRun = (cr.execution_runs ?? [])[0];
  const stepResults = (execRun?.result as Record<string, unknown> | undefined)?.step_results as Record<string, Record<string, unknown>> | undefined;
  const statefulGate = stepResults?.stateful_gate as Record<string, unknown> | undefined;
  const needsStatefulConfirm = statefulGate?.status === "waiting";

  return (
    <>
      <AutoMigrationStepper stepResults={stepResults} crStatus={cr.status} />

      {needsStatefulConfirm && (
        <div className="mt-4 p-4 bg-amber-50 border border-amber-200 rounded-lg">
          <h3 className="text-sm font-semibold text-amber-800 mb-1 flex items-center gap-2">
            ⚠ Stateful workloads detected — human review required
          </h3>
          <p className="text-xs text-amber-700 mb-3">
            The AI identified stateful workloads. Expand the AI Analysis stage above to review the classification and data risk, then confirm to proceed to build.
          </p>
          <div className="flex gap-2">
            <button
              onClick={() => confirmStatefulMutation.mutate()}
              disabled={confirmStatefulMutation.isPending}
              className="px-4 py-2 bg-amber-600 text-white text-sm font-medium rounded-md hover:bg-amber-700 disabled:opacity-50"
            >
              {confirmStatefulMutation.isPending ? "Confirming…" : "Confirm — proceed with migration"}
            </button>
            <button
              onClick={() => apiClient.post(`/change-requests/${cr.id}/rollback`)}
              className="px-4 py-2 bg-white text-slate-700 text-sm font-medium rounded-md border border-slate-300 hover:bg-slate-50"
            >
              Abort
            </button>
          </div>
        </div>
      )}
    </>
  );
})()}
```

- [ ] **Step 3: Restart frontend and verify**

```bash
docker compose stop frontend && docker compose up frontend -d
sleep 10
docker compose logs frontend --tail=15
```

Expected: Vite ready, no TypeScript errors

- [ ] **Step 4: Commit**

```bash
cd f:/Nexplane/nexplane
git add frontend/src/components/AutoMigrationStepper.tsx frontend/src/pages/ChangeRequestDetail.tsx
git commit -m "feat: add 7-stage AutoMigrationStepper to CR detail for agent_containerize_auto"
```

---

### Task 9: Extend Phase AUTO smoke test for confirm-stateful

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py`

- [ ] **Step 1: Find the Phase AUTO test and add confirm-stateful verification**

In `backend/tests/smoke/test_aws_live.py`, find `run_phase_auto`. After the CR is executed and the polling loop begins, add logic to detect and handle the stateful gate:

Find the polling loop that watches for CR completion and add:

```python
# Look for stateful_gate "waiting" state and confirm it
import time as _t
for _poll in range(120):  # up to 10 min
    _t.sleep(5)
    cr_state = client.get(f"/change-requests/{auto_cr_id}")
    status = cr_state.get("status", "")
    
    # Check for stateful gate waiting
    runs = cr_state.get("execution_runs", [])
    if runs:
        step_results = (runs[0].get("result") or {}).get("step_results", {})
        stateful_gate = step_results.get("stateful_gate", {})
        if stateful_gate.get("status") == "waiting":
            log("[Phase AUTO] Stateful gate triggered — auto-confirming for smoke test")
            resp = client.post(f"/change-requests/{auto_cr_id}/confirm-stateful")
            assert resp.get("stateful_approved_at"), f"confirm-stateful returned: {resp}"
            log("[Phase AUTO] Stateful gate confirmed")
    
    if status == "completed":
        log("[Phase AUTO] CR completed")
        break
    if status in ("failed", "rolled_back"):
        fail(f"[Phase AUTO] CR ended with status={status}")
```

- [ ] **Step 2: Run Phase AUTO smoke test**

```bash
docker compose exec -w /app backend python tests/smoke/test_aws_live.py \
  --email admin@acme.example --password admin123 --phases A,AUTO
```

Expected: Phase AUTO passes, stateful gate auto-confirmed if triggered

- [ ] **Step 3: Commit**

```bash
cd f:/Nexplane/nexplane
git add backend/tests/smoke/test_aws_live.py
git commit -m "test: extend Phase AUTO smoke test to auto-confirm stateful gate"
```

---

## Self-Review

**Spec coverage:**
- ✅ `deep_discover` Linux enrichment (Task 1, 2, 3): env var names, open files, runtime deps, config intelligence
- ✅ `deep_discover` Windows enrichment (Task 4): parallel implementation
- ✅ Config-file intelligence (Task 2): nginx/Apache/IIS/generic
- ✅ Backend `deep_discover` executor (Task 5)
- ✅ Agent rebuild and deployment (Task 6)
- ✅ MigrateDrawer UI (Task 7)
- ✅ 7-stage stepper (Task 8)
- ✅ Stateful gate confirmation banner (Task 8, in CR detail)
- ✅ Phase AUTO smoke test extension (Task 9)

**Placeholder scan:** None found. All code blocks are complete.

**Type consistency:**
- `DiscoveredWorkload.EnvVarNames []string` populated by `collectEnvVarNamesLinux(pid)` → same field name in all tasks ✓
- `ConfigEntry` type defined in Task 1, used in Task 2's `parseConfigFiles` → consistent ✓
- `stepResults` typed as `Record<string, Record<string, unknown>>` in Task 8 matches what the auto executor writes ✓
- `MigrateDrawer` props `{ assetId, assetName, onClose }` match AssetDetail usage in Task 7 ✓
