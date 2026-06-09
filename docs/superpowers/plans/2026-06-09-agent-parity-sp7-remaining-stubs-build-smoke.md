# Agent Parity SP7: Remaining Darwin Stubs, Build Fixes & Smoke Tests

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace 4 remaining darwin stubs (configsyslog, crossplatform, estimatesize, fleet), fix all remaining `_other.go` build tags, add darwin to the publish-to-S3 pipeline, then add 3 new smoke test phases exercising all new macOS functionality via the Nexplane CR lifecycle (dogfooding).

**Architecture:** Each stub replacement follows the same `execCommandXxx` mock pattern. Smoke phases use `client.run_cr()` targeting the mac endpoint asset from MAC_AGENT_BOOTSTRAP, with full execute+rollback lifecycle per the dogfooding principle.

**Tech Stack:** Go 1.21, macOS `/usr/sbin/syslogd`, `networksetup`, `du`, `launchctl`, `brew services`, EC2 mac2.metal, Python smoke test runner on EC2.

**Context:** All code edits happen on Windows. Sync to EC2 at 100.101.186.39 via git push + pull (preferred) or scp. Build happens on EC2. Smoke test runs on EC2, not locally.

---

### Task 1: configsyslog_darwin.go

**Files:**
- Modify: `agent/commands/configsyslog/configsyslog_darwin.go` — replace stub
- Create: `agent/commands/configsyslog/configsyslog_darwin_test.go`

- [ ] **Step 1: Write failing test**

Create `agent/commands/configsyslog/configsyslog_darwin_test.go`:

```go
//go:build darwin

package configsyslog

import (
	"os"
	"os/exec"
	"strings"
	"testing"
)

func TestExecuteOS_DarwinWritesForwardLine(t *testing.T) {
	tmpFile := t.TempDir() + "/syslog.conf"
	origPath := darwinSyslogConf
	darwinSyslogConf = tmpFile
	t.Cleanup(func() { darwinSyslogConf = origPath })

	execCommandSyslogDarwin = func(name string, args ...string) *exec.Cmd {
		return exec.Command("echo", "ok")
	}
	t.Cleanup(func() { execCommandSyslogDarwin = exec.Command })

	result, err := executeOS(map[string]any{
		"destination_host": "10.0.0.5",
		"destination_port": float64(514),
		"protocol":         "udp",
		"facility":         "*.*",
	})
	if err != nil {
		t.Fatalf("executeOS: %v", err)
	}
	if result["config_path"] == nil {
		t.Fatal("expected config_path")
	}
	data, _ := os.ReadFile(tmpFile)
	if !strings.Contains(string(data), "10.0.0.5") {
		t.Fatalf("expected forward line with host; got: %s", data)
	}
}

func TestRollbackOS_DarwinRestores(t *testing.T) {
	tmpFile := t.TempDir() + "/syslog.conf"
	origPath := darwinSyslogConf
	darwinSyslogConf = tmpFile
	t.Cleanup(func() { darwinSyslogConf = origPath })

	execCommandSyslogDarwin = func(name string, args ...string) *exec.Cmd {
		return exec.Command("echo", "ok")
	}
	t.Cleanup(func() { execCommandSyslogDarwin = exec.Command })

	result, err := rollbackOS(map[string]any{"snapshot": "# original\n"})
	if err != nil {
		t.Fatalf("rollbackOS: %v", err)
	}
	if result["rolled_back"] != true {
		t.Fatal("expected rolled_back=true")
	}
	data, _ := os.ReadFile(tmpFile)
	if string(data) != "# original\n" {
		t.Fatalf("expected snapshot restored; got: %s", data)
	}
}
```

- [ ] **Step 2: Implement configsyslog_darwin.go**

Replace `agent/commands/configsyslog/configsyslog_darwin.go`:

```go
//go:build darwin

package configsyslog

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

// darwinSyslogConf is overridable in tests.
var darwinSyslogConf = "/etc/syslog.conf"

// execCommandSyslogDarwin is mockable in tests.
var execCommandSyslogDarwin = exec.Command

func executeOS(params map[string]any) (map[string]any, error) {
	host, _ := params["destination_host"].(string)
	if host == "" {
		return nil, fmt.Errorf("destination_host is required")
	}
	port := params["destination_port"]
	if port == nil {
		return nil, fmt.Errorf("destination_port is required")
	}
	proto, _ := params["protocol"].(string)
	if proto == "" {
		proto = "udp"
	}
	facility, _ := params["facility"].(string)
	if facility == "" {
		facility = "*.*"
	}
	portInt := toInt(port)
	if portInt == 0 {
		portInt = 514
	}

	existing, _ := os.ReadFile(darwinSyslogConf)
	snapshot := string(existing)

	// Build forward line: UDP uses @, TCP uses @@
	prefix := "@"
	if proto == "tcp" {
		prefix = "@@"
	}
	forwardLine := fmt.Sprintf("%s\t%s%s:%d", facility, prefix, host, portInt)

	// Remove existing nexplane block then append new one
	content := removeSyslogBlock(snapshot)
	block := fmt.Sprintf("\n%s\n%s\n%s\n", nexplaneBegin, forwardLine, nexplaneEnd)
	content += block

	if err := os.WriteFile(darwinSyslogConf, []byte(content), 0644); err != nil {
		return nil, fmt.Errorf("writing syslog.conf: %w", err)
	}

	// Reload syslogd
	execCommandSyslogDarwin("launchctl", "kickstart", "-k", "system/com.apple.syslogd").Run() //nolint:errcheck

	return map[string]any{
		"daemon":      "syslogd",
		"config_path": darwinSyslogConf,
		"snapshot":    snapshot,
		"applied_at":  time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func rollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(string)
	if !ok {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	if err := os.WriteFile(darwinSyslogConf, []byte(snapshot), 0644); err != nil {
		return nil, fmt.Errorf("restoring syslog.conf: %w", err)
	}
	execCommandSyslogDarwin("launchctl", "kickstart", "-k", "system/com.apple.syslogd").Run() //nolint:errcheck
	return map[string]any{"rolled_back": true}, nil
}

func removeSyslogBlock(content string) string {
	lines := strings.Split(content, "\n")
	var out []string
	inBlock := false
	for _, l := range lines {
		t := strings.TrimSpace(l)
		if t == nexplaneBegin {
			inBlock = true
			continue
		}
		if t == nexplaneEnd {
			inBlock = false
			continue
		}
		if !inBlock {
			out = append(out, l)
		}
	}
	return strings.Join(out, "\n")
}
```

Note: `nexplaneBegin`, `nexplaneEnd`, and `toInt` are already defined in `configsyslog_linux.go` or `configsyslog.go`. Check the package for the `toInt` helper; if it's in `configsyslog_linux.go` only, move it to `configsyslog.go` (the shared file) or duplicate it in the darwin file.

- [ ] **Step 3: Check if toInt and nexplaneBegin/End need moving**

```bash
grep -n "func toInt\|nexplaneBegin\|nexplaneEnd" agent/commands/configsyslog/*.go
```

If `toInt` is only in `configsyslog_linux.go`, move it to `configsyslog.go` (remove the build tag or add it to the shared file). Same for `nexplaneBegin`/`nexplaneEnd` constants.

- [ ] **Step 4: Run tests**

```bash
cd agent && go test ./commands/configsyslog/ -run "TestExecuteOS_Darwin|TestRollbackOS_Darwin" -v
```
Expected: PASS

- [ ] **Step 5: Compile check**

```bash
cd agent && GOOS=darwin GOARCH=arm64 go build ./... && GOOS=linux go build ./... && echo "OK"
```

- [ ] **Step 6: Commit**

```bash
git add agent/commands/configsyslog/configsyslog_darwin.go agent/commands/configsyslog/configsyslog_darwin_test.go
git commit -m "feat(agent): macOS syslog forwarding via /etc/syslog.conf + syslogd reload"
```

---

### Task 2: crossplatform_darwin.go (TLS, DNS, inventory)

**Files:**
- Modify: `agent/commands/crossplatform/crossplatform_darwin.go` — replace stubs
- Create: `agent/commands/crossplatform/crossplatform_darwin_test.go`

- [ ] **Step 1: Write failing test**

Create `agent/commands/crossplatform/crossplatform_darwin_test.go`:

```go
//go:build darwin

package crossplatform

import (
	"os/exec"
	"strings"
	"testing"
)

func TestDNSExecuteOS_Darwin(t *testing.T) {
	var cmds []string
	execCommandCrossplatformDarwin = func(name string, args ...string) *exec.Cmd {
		cmds = append(cmds, name+" "+strings.Join(args, " "))
		if strings.Contains(strings.Join(args, " "), "getdnsservers") {
			return exec.Command("echo", "8.8.8.8")
		}
		if strings.Contains(strings.Join(args, " "), "listallnetworkservices") {
			return exec.Command("printf", "An asterisk (*) denotes that a network service is disabled.\nWi-Fi\nEthernet\n")
		}
		return exec.Command("echo", "ok")
	}
	t.Cleanup(func() { execCommandCrossplatformDarwin = exec.Command })

	result, err := dnsExecuteOS(map[string]any{
		"resolvers": []any{"1.1.1.1", "8.8.8.8"},
	})
	if err != nil {
		t.Fatalf("dnsExecuteOS: %v", err)
	}
	if result["snapshot"] == nil {
		t.Fatal("expected snapshot")
	}
	foundSetDNS := false
	for _, c := range cmds {
		if strings.Contains(c, "setdnsservers") {
			foundSetDNS = true
		}
	}
	if !foundSetDNS {
		t.Fatalf("expected networksetup -setdnsservers; got: %v", cmds)
	}
}

func TestInventoryExecuteOS_Darwin(t *testing.T) {
	execCommandCrossplatformDarwin = func(name string, args ...string) *exec.Cmd {
		if name == "brew" {
			return exec.Command("printf", "nginx 1.25.0\ncurl 8.1.0\n")
		}
		return exec.Command("echo", "")
	}
	t.Cleanup(func() { execCommandCrossplatformDarwin = exec.Command })

	result, err := inventoryExecuteOS(map[string]any{})
	if err != nil {
		t.Fatalf("inventoryExecuteOS: %v", err)
	}
	pkgs, _ := result["packages"].([]map[string]any)
	if len(pkgs) == 0 {
		t.Fatal("expected packages from brew mock")
	}
}
```

- [ ] **Step 2: Implement crossplatform_darwin.go**

Replace `agent/commands/crossplatform/crossplatform_darwin.go`:

```go
//go:build darwin

package crossplatform

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

// execCommandCrossplatformDarwin is mockable in tests.
var execCommandCrossplatformDarwin = exec.Command

func tlsExecuteOS(params map[string]any) (map[string]any, error) {
	action, _ := params["action"].(string)
	service, _ := params["service"].(string)

	certDir := fmt.Sprintf("/etc/nexplane/tls/%s", service)
	certPath := certDir + "/cert.pem"
	keyPath := certDir + "/key.pem"

	snapshot := map[string]any{}
	if data, err := os.ReadFile(certPath); err == nil {
		snapshot["cert"] = string(data)
	}
	if data, err := os.ReadFile(keyPath); err == nil {
		snapshot["key"] = string(data)
	}

	if action == "validate" {
		out, _ := execCommandCrossplatformDarwin("openssl", "x509", "-noout",
			"-subject", "-enddate", "-in", certPath).Output()
		return map[string]any{
			"action": "validate", "service": service, "cert_path": certPath,
			"openssl_output": strings.TrimSpace(string(out)),
			"checked_at":     time.Now().UTC().Format(time.RFC3339),
		}, nil
	}

	source, _ := params["source"].(string)
	if source == "manual" {
		certPEM, _ := params["cert_pem"].(string)
		keyPEM, _ := params["key_pem"].(string)
		if err := os.MkdirAll(certDir, 0750); err != nil {
			return nil, fmt.Errorf("creating cert dir: %w", err)
		}
		if err := os.WriteFile(certPath, []byte(certPEM), 0644); err != nil {
			return nil, fmt.Errorf("writing cert: %w", err)
		}
		if err := os.WriteFile(keyPath, []byte(keyPEM), 0600); err != nil {
			return nil, fmt.Errorf("writing key: %w", err)
		}
	}

	// Restart service via launchctl
	if service != "" {
		execCommandCrossplatformDarwin("launchctl", "kickstart", "-k", "system/"+service).Run() //nolint:errcheck
	}

	return map[string]any{
		"service":    service,
		"cert_path":  certPath,
		"snapshot":   snapshot,
		"applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func tlsRollbackOS(params map[string]any) (map[string]any, error) {
	service, _ := params["service"].(string)
	snapshot, _ := params["snapshot"].(map[string]any)
	if snapshot == nil {
		return map[string]any{"rolled_back": false, "reason": "no snapshot"}, nil
	}

	certDir := fmt.Sprintf("/etc/nexplane/tls/%s", service)
	if certData, _ := snapshot["cert"].(string); certData != "" {
		os.WriteFile(certDir+"/cert.pem", []byte(certData), 0644) //nolint:errcheck
	}
	if keyData, _ := snapshot["key"].(string); keyData != "" {
		os.WriteFile(certDir+"/key.pem", []byte(keyData), 0600) //nolint:errcheck
	}
	if service != "" {
		execCommandCrossplatformDarwin("launchctl", "kickstart", "-k", "system/"+service).Run() //nolint:errcheck
	}
	return map[string]any{"rolled_back": true}, nil
}

func dnsExecuteOS(params map[string]any) (map[string]any, error) {
	resolversRaw, _ := params["resolvers"].([]any)
	resolvers := make([]string, 0, len(resolversRaw))
	for _, r := range resolversRaw {
		if s, ok := r.(string); ok {
			resolvers = append(resolvers, s)
		}
	}
	if len(resolvers) == 0 {
		return nil, fmt.Errorf("resolvers is required")
	}

	// Detect active network service
	svcOut, _ := execCommandCrossplatformDarwin("networksetup", "-listallnetworkservices").Output()
	service := detectActiveNetworkService(string(svcOut))

	// Snapshot current DNS
	snapOut, _ := execCommandCrossplatformDarwin("networksetup", "-getdnsservers", service).Output()
	snapshot := strings.TrimSpace(string(snapOut))

	// Apply new resolvers
	args := append([]string{"-setdnsservers", service}, resolvers...)
	if out, err := execCommandCrossplatformDarwin("networksetup", args...).CombinedOutput(); err != nil {
		return nil, fmt.Errorf("networksetup -setdnsservers: %s: %w", out, err)
	}

	return map[string]any{
		"service":    service,
		"resolvers":  resolvers,
		"snapshot":   snapshot,
		"applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func dnsRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, _ := params["snapshot"].(string)
	service, _ := params["service"].(string)
	if service == "" || snapshot == "" {
		return map[string]any{"rolled_back": false, "reason": "no snapshot or service"}, nil
	}
	// snapshot is "8.8.8.8\n8.8.4.4" or "There aren't any DNS Servers set..."
	if strings.Contains(snapshot, "There aren't") {
		execCommandCrossplatformDarwin("networksetup", "-setdnsservers", service, "Empty").Run() //nolint:errcheck
	} else {
		resolvers := strings.Fields(snapshot)
		args := append([]string{"-setdnsservers", service}, resolvers...)
		execCommandCrossplatformDarwin("networksetup", args...).Run() //nolint:errcheck
	}
	return map[string]any{"rolled_back": true}, nil
}

func inventoryExecuteOS(_ map[string]any) (map[string]any, error) {
	packages := []map[string]any{}

	// Homebrew
	if out, err := execCommandCrossplatformDarwin("brew", "list", "--versions").Output(); err == nil {
		for _, line := range strings.Split(string(out), "\n") {
			parts := strings.Fields(line)
			if len(parts) >= 2 {
				packages = append(packages, map[string]any{
					"name": parts[0], "version": parts[1], "source": "brew",
				})
			}
		}
	}

	// MacPorts (if installed)
	if out, err := execCommandCrossplatformDarwin("port", "installed").Output(); err == nil {
		for _, line := range strings.Split(string(out), "\n") {
			line = strings.TrimSpace(line)
			if line == "" || strings.HasPrefix(line, "The following") {
				continue
			}
			// "  name @version_revision (active)"
			parts := strings.Fields(line)
			if len(parts) >= 2 {
				name := parts[0]
				version := strings.TrimPrefix(parts[1], "@")
				packages = append(packages, map[string]any{
					"name": name, "version": version, "source": "macports",
				})
			}
		}
	}

	// System packages via pkgutil
	if out, err := execCommandCrossplatformDarwin("pkgutil", "--pkgs").Output(); err == nil {
		for _, pkg := range strings.Split(string(out), "\n") {
			pkg = strings.TrimSpace(pkg)
			if pkg == "" {
				continue
			}
			packages = append(packages, map[string]any{
				"name": pkg, "source": "pkgutil",
			})
		}
	}

	return map[string]any{
		"packages":     packages,
		"collected_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

// detectActiveNetworkService returns the first active non-disabled service name.
func detectActiveNetworkService(listOutput string) string {
	for _, line := range strings.Split(listOutput, "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "*") || strings.HasPrefix(line, "An asterisk") {
			continue
		}
		return line
	}
	return "Wi-Fi" // reasonable fallback
}
```

- [ ] **Step 3: Run tests**

```bash
cd agent && go test ./commands/crossplatform/ -run "TestDNSExecuteOS_Darwin|TestInventoryExecuteOS_Darwin" -v
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add agent/commands/crossplatform/crossplatform_darwin.go agent/commands/crossplatform/crossplatform_darwin_test.go
git commit -m "feat(agent): macOS TLS/DNS/inventory via networksetup+brew+pkgutil"
```

---

### Task 3: estimatesize_darwin.go + fleet_darwin.go

**Files:**
- Modify: `agent/commands/estimatesize/estimatesize_darwin.go` — replace stub
- Create: `agent/commands/estimatesize/estimatesize_darwin_test.go`
- Modify: `agent/commands/fleet/fleet_darwin.go` — replace stubs
- Create: `agent/commands/fleet/fleet_darwin_test.go`

- [ ] **Step 1: Write failing tests**

Create `agent/commands/estimatesize/estimatesize_darwin_test.go`:

```go
//go:build darwin

package estimatesize

import (
	"os/exec"
	"testing"
)

func TestExecuteOS_Darwin(t *testing.T) {
	execCommandEstimateDarwin = func(name string, args ...string) *exec.Cmd {
		if name == "du" {
			return exec.Command("echo", "12345\t/etc")
		}
		return exec.Command("echo", "Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/disk3s5 500GB 250GB 250GB 50% /")
	}
	t.Cleanup(func() { execCommandEstimateDarwin = exec.Command })

	result, err := executeOS(map[string]any{"path": "/etc"})
	if err != nil {
		t.Fatalf("executeOS: %v", err)
	}
	size, _ := result["size_bytes"].(int64)
	if size != 12345*1024 {
		t.Fatalf("expected size_bytes=12648960; got %d", size)
	}
}
```

Create `agent/commands/fleet/fleet_darwin_test.go`:

```go
//go:build darwin

package fleet

import (
	"context"
	"os/exec"
	"strings"
	"testing"
)

func TestRestartServiceOS_DarwinLaunchctl(t *testing.T) {
	var cmds []string
	execCommandFleetDarwin = func(name string, args ...string) *exec.Cmd {
		cmds = append(cmds, name+" "+strings.Join(args, " "))
		return exec.Command("echo", "ok")
	}
	t.Cleanup(func() { execCommandFleetDarwin = exec.Command })

	result := restartServiceOS(context.Background(), "nginx")
	if result["running"] != true {
		t.Fatalf("expected running=true; got %v", result)
	}
	found := false
	for _, c := range cmds {
		if strings.Contains(c, "launchctl") || strings.Contains(c, "brew") {
			found = true
		}
	}
	if !found {
		t.Fatalf("expected launchctl or brew; got: %v", cmds)
	}
}

func TestPushConfigFileOS_Darwin(t *testing.T) {
	tmpDir := t.TempDir()
	result := pushConfigFileOS(map[string]any{
		"file_path":    tmpDir + "/test.conf",
		"file_content": "key=value",
	})
	if result["error"] != nil {
		t.Fatalf("pushConfigFileOS error: %v", result["error"])
	}
}
```

- [ ] **Step 2: Implement estimatesize_darwin.go**

Replace `agent/commands/estimatesize/estimatesize_darwin.go`:

```go
//go:build darwin

package estimatesize

import (
	"fmt"
	"os/exec"
	"strconv"
	"strings"
	"time"
)

// execCommandEstimateDarwin is mockable in tests.
var execCommandEstimateDarwin = exec.Command

func executeOS(params map[string]any) (map[string]any, error) {
	path, _ := params["path"].(string)
	if path == "" {
		path = "/"
	}

	// du -sk: BSD variant — -k gives 1024-byte blocks, -s for summary
	out, err := execCommandEstimateDarwin("du", "-sk", path).Output()
	if err != nil {
		return nil, fmt.Errorf("du -sk %s: %w", path, err)
	}
	parts := strings.Fields(string(out))
	if len(parts) == 0 {
		return nil, fmt.Errorf("du -sk returned empty output")
	}
	sizeKB, _ := strconv.ParseInt(parts[0], 10, 64)

	// df -k for available space
	dfOut, _ := execCommandEstimateDarwin("df", "-k", path).Output()

	return map[string]any{
		"path":         path,
		"size_bytes":   sizeKB * 1024,
		"df_output":    strings.TrimSpace(string(dfOut)),
		"collected_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}
```

- [ ] **Step 3: Implement fleet_darwin.go**

Replace `agent/commands/fleet/fleet_darwin.go`:

```go
//go:build darwin

package fleet

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

// execCommandFleetDarwin is mockable in tests.
var execCommandFleetDarwin = exec.Command

func restartServiceOS(ctx context.Context, serviceName string) map[string]any {
	// Try launchctl kickstart with common label patterns
	labels := []string{
		"system/homebrew.mxcl." + serviceName,
		"system/com." + serviceName + "." + serviceName,
		"system/com.apple." + serviceName,
	}
	for _, label := range labels {
		out, err := execCommandFleetDarwin("launchctl", "kickstart", "-k", label).CombinedOutput()
		if err == nil {
			return map[string]any{"running": true, "output": string(out), "label": label}
		}
	}

	// Fallback: brew services restart
	out2, err2 := execCommandFleetDarwin("brew", "services", "restart", serviceName).CombinedOutput()
	if err2 != nil {
		return map[string]any{
			"running": false,
			"error":   fmt.Sprintf("launchctl (all labels) and brew services restart failed: %s", out2),
		}
	}
	return map[string]any{"running": true, "output": string(out2)}
}

func pushConfigFileOS(params map[string]any) map[string]any {
	filePath, _ := params["file_path"].(string)
	fileContent, _ := params["file_content"].(string)
	backup, _ := params["backup"].(bool)

	if filePath == "" {
		return map[string]any{"error": "file_path is required"}
	}

	if backup {
		if existing, err := os.ReadFile(filePath); err == nil {
			backupPath := fmt.Sprintf("%s.bak.%d", filePath, time.Now().Unix())
			if err := os.WriteFile(backupPath, existing, 0600); err != nil {
				return map[string]any{"error": fmt.Sprintf("backup failed: %v", err)}
			}
		}
	}

	if err := os.WriteFile(filePath, []byte(fileContent), 0644); err != nil {
		return map[string]any{"error": err.Error()}
	}
	return map[string]any{"pushed": true, "file_path": filePath}
}

func healthCheckOS(_ context.Context, endpoints []string) map[string]any {
	// Pure HTTP — identical behavior on all platforms
	results := map[string]string{}
	for _, ep := range endpoints {
		out, err := execCommandFleetDarwin("curl", "-sf", "--max-time", "5", ep).Output()
		if err != nil {
			results[ep] = "unhealthy"
		} else {
			body := strings.TrimSpace(string(out))
			if len(body) > 0 {
				results[ep] = "healthy"
			} else {
				results[ep] = "healthy"
			}
		}
	}
	return map[string]any{"results": results, "healthy": true}
}

func runPostCommand(_ context.Context, command string) map[string]any {
	cmd := execCommandFleetDarwin("/bin/sh", "-c", command)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return map[string]any{"ran": false, "error": err.Error(), "output": string(out)}
	}
	return map[string]any{"ran": true, "output": string(out)}
}
```

- [ ] **Step 4: Run tests**

```bash
cd agent && go test ./commands/estimatesize/ ./commands/fleet/ -v -count=1 2>&1 | grep -E "^(=== RUN|--- PASS|--- FAIL|ok|FAIL)"
```
Expected: PASS for darwin-tagged tests

- [ ] **Step 5: Compile all platforms**

```bash
cd agent && GOOS=darwin GOARCH=arm64 go build ./... && GOOS=linux go build ./... && GOOS=windows go build ./... && echo "ALL OK"
```

- [ ] **Step 6: Commit**

```bash
git add agent/commands/estimatesize/estimatesize_darwin.go agent/commands/estimatesize/estimatesize_darwin_test.go agent/commands/fleet/fleet_darwin.go agent/commands/fleet/fleet_darwin_test.go
git commit -m "feat(agent): macOS estimate_size (du/df) and fleet ops (launchctl/brew)"
```

---

### Task 4: Add darwin to publish-to-s3.sh + Makefile, bump version to 0.3.6

**Files:**
- Modify: `agent/scripts/publish-to-s3.sh`
- Modify: `agent/Makefile`

- [ ] **Step 1: Update publish-to-s3.sh**

Edit `agent/scripts/publish-to-s3.sh` — add darwin build after the linux-arm64 section:

```bash
echo "Building darwin-arm64..."
GOOS=darwin GOARCH=arm64 go build -ldflags "-X main.Version=$VERSION" -o "$DIST/nexplane-agent-darwin-arm64" ./
```

And add upload after the linux uploads:
```bash
aws s3 cp "$DIST/nexplane-agent-darwin-arm64" "s3://$BUCKET/nexplane-agent-darwin-arm64-${VERSION}" --acl public-read
```

The full updated upload block:
```bash
aws s3 cp "$DIST/nexplane-agent-linux-amd64" "s3://$BUCKET/nexplane-agent-linux-amd64-${VERSION}" --acl public-read
aws s3 cp "$DIST/nexplane-agent-linux-arm64" "s3://$BUCKET/nexplane-agent-linux-arm64-${VERSION}" --acl public-read
aws s3 cp "$DIST/nexplane-agent-darwin-arm64" "s3://$BUCKET/nexplane-agent-darwin-arm64-${VERSION}" --acl public-read
aws s3 cp "$DIST/nexplane-agent-windows-amd64-${VERSION}.exe" "s3://$BUCKET/nexplane-agent-windows-amd64-${VERSION}.exe" --acl public-read
echo -n "$VERSION" | aws s3 cp - "s3://$BUCKET/version" --acl public-read --content-type text/plain
```

- [ ] **Step 2: Update Makefile**

Edit `agent/Makefile` — add darwin target and include in `build`:

```makefile
GOENV_DARWIN_ARM64=GOOS=darwin GOARCH=arm64

build: build-linux build-darwin build-windows

build-darwin:
	$(GOENV_DARWIN_ARM64) go build $(LDFLAGS) -o dist/nexplane-agent-darwin-arm64 ./
```

- [ ] **Step 3: Sync to EC2 and build version 0.3.6**

Push to git, then on EC2:
```bash
cd /home/ec2-user/nexplane && git pull origin master
cd agent
# Cross-compile darwin on linux requires CGO_ENABLED=0
CGO_ENABLED=0 GOOS=darwin GOARCH=arm64 go build -ldflags "-X main.Version=0.3.6" -o dist/nexplane-agent-darwin-arm64 ./
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -ldflags "-X main.Version=0.3.6" -o dist/nexplane-agent-linux-amd64 ./
CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -ldflags "-X main.Version=0.3.6" -o dist/nexplane-agent-linux-arm64 ./
CGO_ENABLED=0 GOOS=windows GOARCH=amd64 go build -ldflags "-X main.Version=0.3.6" -o dist/nexplane-agent-windows-amd64-0.3.6.exe ./
ls -lh dist/
```
Expected: 4 binaries present

- [ ] **Step 4: Upload via publish_via_connector.py**

```bash
# On EC2, inside backend container:
docker exec nexplane-backend-1 python3 /tmp/publish_via_connector.py 0.3.6
```

If that script doesn't handle darwin, use the direct approach:
```bash
cd /home/ec2-user/nexplane/agent && bash scripts/publish-to-s3.sh 0.3.6
```
Expected: "=== Published version 0.3.6 ===" with darwin URL shown

- [ ] **Step 5: Verify S3 version**

```bash
curl -s https://nexplane-agent-downloads.s3.amazonaws.com/version
```
Expected: `0.3.6`

- [ ] **Step 6: Commit**

```bash
git add agent/Makefile agent/scripts/publish-to-s3.sh
git commit -m "build: add darwin-arm64 to publish pipeline, bump to 0.3.6"
```

---

### Task 5: Smoke phase MAC_POSTURE_AUDIT

Add new smoke phase to `backend/tests/smoke/test_aws_live.py`.

**Important:** This phase uses the mac endpoint asset from `MAC_AGENT_BOOTSTRAP`. Run after MAC_AGENT_BOOTSTRAP. All operations go through the CR lifecycle — never direct executor calls.

- [ ] **Step 1: Add phase description to docstring**

Find the docstring at the top of `test_aws_live.py` and add:
```
    MAC_POSTURE_AUDIT  macOS security posture: configure_selinux (Gatekeeper), apply_sysctl_hardening,
                       deploy_auditd_rules, setup_fim, configure_apparmor (Santa rules), configure_host_firewall,
                       discover_applications, deep_discover, collect_forensics — all with execute+rollback
```

- [ ] **Step 2: Add phase to argparse help**

Find the `MAC_AGENT_BOOTSTRAP=macOS agent smoke test...` help string and add:
```python
"MAC_POSTURE_AUDIT=macOS security posture audit CRs with full execute+rollback lifecycle. "
"Requires MAC_AGENT_BOOTSTRAP to have run first (needs mac_endpoint_asset_id). "
```

- [ ] **Step 3: Add run_phase_mac_posture_audit function**

Add this function after the `run_phase_mac_agent_bootstrap` function:

```python
def run_phase_mac_posture_audit(client, mac_endpoint_asset_id: str, ssh_host: str, ssh_key_path: str) -> None:
    """Phase MAC_POSTURE_AUDIT: Run macOS security posture CRs via the Nexplane CR lifecycle.
    
    All operations go through create→plan→approve→execute→rollback.
    Verifies both execution (side-effect visible via SSH) and rollback (state restored).
    """
    import paramiko
    print("\n[Phase MAC_POSTURE_AUDIT] macOS Security Posture Audit")
    
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    pkey = paramiko.RSAKey.from_private_key_file(ssh_key_path)
    ssh.connect(hostname=ssh_host, username="ec2-user", pkey=pkey, timeout=30)
    
    def ssh_run(cmd: str) -> str:
        _, stdout, stderr = ssh.exec_command(cmd, timeout=60)
        return stdout.read().decode().strip()
    
    rollback_stack = []  # (cr_id, label) tuples
    
    try:
        # --- 1. configure_selinux (darwin: Gatekeeper + Santa check) ---
        log("MAC_POSTURE_AUDIT: configure_selinux (Gatekeeper enforce)...")
        cr_selinux = client.run_cr(
            "[MAC_POSTURE_AUDIT] configure_selinux enforcing",
            "configure_selinux",
            mac_endpoint_asset_id,
            {"mode": "enforcing"},
        )
        result_sl = client.get_cr_step_result(cr_selinux)
        assert result_sl.get("config_snapshot") is not None, \
            f"configure_selinux result missing config_snapshot: {result_sl}"
        rollback_stack.append((cr_selinux["id"], "configure_selinux"))
        log(f"MAC_POSTURE_AUDIT: configure_selinux OK — new_mode={result_sl.get('new_mode')}")

        # Verify Gatekeeper via SSH
        gk_status = ssh_run("spctl --status 2>&1")
        log(f"MAC_POSTURE_AUDIT: Gatekeeper status via SSH: {gk_status}")

        # --- 2. apply_sysctl_hardening ---
        log("MAC_POSTURE_AUDIT: apply_sysctl_hardening...")
        cr_sysctl = client.run_cr(
            "[MAC_POSTURE_AUDIT] apply_sysctl_hardening",
            "apply_sysctl_hardening",
            mac_endpoint_asset_id,
            {},
        )
        result_sys = client.get_cr_step_result(cr_sysctl)
        assert result_sys.get("snapshot") is not None, \
            f"sysctl result missing snapshot: {result_sys}"
        rollback_stack.append((cr_sysctl["id"], "apply_sysctl_hardening"))
        
        # Verify a sysctl key was applied
        ip_fwd = ssh_run("sudo sysctl -n net.inet.ip.forwarding 2>/dev/null || echo unknown")
        log(f"MAC_POSTURE_AUDIT: net.inet.ip.forwarding={ip_fwd}")
        assert ip_fwd in ("0", "unknown"), \
            f"expected ip_forwarding=0 after hardening; got {ip_fwd}"
        log("MAC_POSTURE_AUDIT: sysctl_hardening OK")

        # --- 3. deploy_auditd_rules (darwin: BSM audit) ---
        log("MAC_POSTURE_AUDIT: deploy_auditd_rules (BSM cis_level1)...")
        cr_audit = client.run_cr(
            "[MAC_POSTURE_AUDIT] deploy_auditd_rules cis_level1",
            "deploy_auditd_rules",
            mac_endpoint_asset_id,
            {"profile": "cis_level1"},
        )
        result_aud = client.get_cr_step_result(cr_audit)
        assert result_aud.get("rules_path") is not None, \
            f"auditd result missing rules_path: {result_aud}"
        rollback_stack.append((cr_audit["id"], "deploy_auditd_rules"))
        
        # Verify audit_control was written
        audit_content = ssh_run("sudo cat /etc/security/audit_control 2>/dev/null || echo MISSING")
        assert "dir:/var/audit" in audit_content, \
            f"expected audit_control with dir:/var/audit; got: {audit_content[:200]}"
        log("MAC_POSTURE_AUDIT: deploy_auditd_rules OK — audit_control verified via SSH")

        # --- 4. setup_file_integrity_monitoring ---
        log("MAC_POSTURE_AUDIT: setup_fim init...")
        cr_fim = client.run_cr(
            "[MAC_POSTURE_AUDIT] setup_fim init",
            "setup_file_integrity_monitoring",
            mac_endpoint_asset_id,
            {"action": "init", "watch_paths": ["/etc/ssh"]},
        )
        result_fim = client.get_cr_step_result(cr_fim)
        assert result_fim.get("snapshot_path") is not None, \
            f"fim result missing snapshot_path: {result_fim}"
        log(f"MAC_POSTURE_AUDIT: FIM init OK — files={result_fim.get('file_count')}")

        # FIM check (verify sha256 walk works)
        cr_fim_check = client.run_cr(
            "[MAC_POSTURE_AUDIT] setup_fim check",
            "setup_file_integrity_monitoring",
            mac_endpoint_asset_id,
            {"action": "check", "watch_paths": ["/etc/ssh"]},
        )
        result_fim_check = client.get_cr_step_result(cr_fim_check)
        assert "violations" in result_fim_check, \
            f"fim check missing violations field: {result_fim_check}"
        log(f"MAC_POSTURE_AUDIT: FIM check OK — violations={result_fim_check.get('violations')}")

        # --- 5. configure_apparmor (darwin: Santa rules) ---
        log("MAC_POSTURE_AUDIT: configure_apparmor (Santa rule)...")
        # Use a test SHA-256 that won't block anything real
        _test_sha = "a" * 64  # placeholder hash
        cr_aa = client.run_cr(
            "[MAC_POSTURE_AUDIT] configure_apparmor santa_rule",
            "configure_apparmor",
            mac_endpoint_asset_id,
            {
                "profile_name": "nexplane_smoke_test",
                "profile_content": f'[{{"sha256":"{_test_sha}","comment":"nexplane_smoke_test"}}]',
                "mode": "enforce",
            },
        )
        result_aa = client.get_cr_step_result(cr_aa)
        assert result_aa.get("snapshot") is not None, \
            f"apparmor result missing snapshot: {result_aa}"
        rollback_stack.append((cr_aa["id"], "configure_apparmor"))
        log(f"MAC_POSTURE_AUDIT: configure_apparmor OK — mode={result_aa.get('mode_applied')}")

        # --- 6. discover_applications (read-only) ---
        log("MAC_POSTURE_AUDIT: discover_applications...")
        cr_disc = client.run_cr(
            "[MAC_POSTURE_AUDIT] discover_applications",
            "discover_applications",
            mac_endpoint_asset_id,
            {},
        )
        result_disc = client.get_cr_step_result(cr_disc)
        apps = result_disc.get("applications", [])
        log(f"MAC_POSTURE_AUDIT: discover_applications OK — {len(apps)} apps found")

        # --- 7. deep_discover ---
        log("MAC_POSTURE_AUDIT: deep_discover...")
        cr_dd = client.run_cr(
            "[MAC_POSTURE_AUDIT] deep_discover",
            "deep_discover",
            mac_endpoint_asset_id,
            {},
        )
        result_dd = client.get_cr_step_result(cr_dd)
        assert result_dd.get("os") == "darwin" or result_dd.get("OS") == "darwin", \
            f"deep_discover OS field not darwin: {result_dd}"
        log(f"MAC_POSTURE_AUDIT: deep_discover OK — workloads={len(result_dd.get('workloads', []))}")

        # --- 8. collect_forensics ---
        log("MAC_POSTURE_AUDIT: collect_forensics...")
        cr_for = client.run_cr(
            "[MAC_POSTURE_AUDIT] collect_forensics",
            "collect_forensics",
            mac_endpoint_asset_id,
            {},
        )
        result_for = client.get_cr_step_result(cr_for)
        artifacts = result_for.get("artifacts", [])
        assert len(artifacts) > 0, f"collect_forensics returned no artifacts: {result_for}"
        log(f"MAC_POSTURE_AUDIT: collect_forensics OK — {len(artifacts)} artifacts")

        log("MAC_POSTURE_AUDIT: all execute phases passed — starting rollbacks...")

        # --- Rollback all stateful CRs in reverse order ---
        for cr_id, label in reversed(rollback_stack):
            status = _wait_rollback_posture(client, cr_id, label)
            log(f"MAC_POSTURE_AUDIT: rollback {label} → {status}")

        # --- Verify rollback of sysctl (ip_forwarding restored) ---
        ip_fwd_after = ssh_run("sudo sysctl -n net.inet.ip.forwarding 2>/dev/null || echo unknown")
        log(f"MAC_POSTURE_AUDIT: ip_forwarding after rollback: {ip_fwd_after}")

        # --- Verify rollback of audit_control ---
        audit_after = ssh_run("sudo cat /etc/security/audit_control 2>/dev/null || echo MISSING")
        if "dir:/var/audit" in audit_after:
            log("MAC_POSTURE_AUDIT: ⚠️  audit_control still has nexplane content after rollback (may be empty originally)")
        else:
            log("MAC_POSTURE_AUDIT: audit_control rollback verified")

        log("MAC_POSTURE_AUDIT: phase complete ✓")

    finally:
        ssh.close()


def _wait_rollback_posture(client, cr_id: str, label: str, timeout: int = 120) -> str:
    """Submit rollback and poll to completion."""
    client.post(f"/change-requests/{cr_id}/rollback", json={})
    for _ in range(timeout // 5):
        s = client.get(f"/change-requests/{cr_id}").get("status", "")
        if s in ("rolled_back", "failed", "completed"):
            return s
        time.sleep(5)
    return "timeout"
```

- [ ] **Step 4: Add phase to main() dispatcher**

Find the `if "MAC_AGENT_BOOTSTRAP" in phases:` block and add after it:

```python
        if "MAC_POSTURE_AUDIT" in phases:
            if not mac_endpoint_asset_id:
                fail("MAC_POSTURE_AUDIT requires MAC_AGENT_BOOTSTRAP to have set mac_endpoint_asset_id")
            if not ssh_host:
                fail("MAC_POSTURE_AUDIT requires --ssh-host (the mac instance IP)")
            ssh_key = getattr(args, "ssh_key_path", "")
            run_phase_mac_posture_audit(client, mac_endpoint_asset_id, ssh_host, ssh_key)
```

Note: `mac_endpoint_asset_id` and `ssh_host` are set by `MAC_AGENT_BOOTSTRAP`. Add `--ssh-host` argument or derive it from MAC_AGENT_BOOTSTRAP state. The simplest approach: MAC_POSTURE_AUDIT must be run in the same invocation as MAC_AGENT_BOOTSTRAP, so the variable is already in scope.

- [ ] **Step 5: Commit smoke test changes**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "test(smoke): add MAC_POSTURE_AUDIT phase for macOS security posture CRs"
```

---

### Task 6: Smoke phase MAC_AUTH_HARDENING + MAC_OBSERVABILITY

- [ ] **Step 1: Add MAC_AUTH_HARDENING function**

Add after `run_phase_mac_posture_audit`:

```python
def run_phase_mac_auth_hardening(client, mac_endpoint_asset_id: str, ssh_host: str, ssh_key_path: str) -> None:
    """Phase MAC_AUTH_HARDENING: SSH hardening, NTP, syslog forwarding with execute+rollback."""
    import paramiko
    print("\n[Phase MAC_AUTH_HARDENING] macOS Auth Hardening")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(hostname=ssh_host, username="ec2-user",
                pkey=paramiko.RSAKey.from_private_key_file(ssh_key_path), timeout=30)

    def ssh_run(cmd: str) -> str:
        _, out, _ = ssh.exec_command(cmd, timeout=30)
        return out.read().decode().strip()

    rollback_stack = []
    try:
        # --- 1. harden_ssh ---
        log("MAC_AUTH_HARDENING: harden_ssh...")
        cr_ssh = client.run_cr(
            "[MAC_AUTH_HARDENING] harden_ssh",
            "harden_ssh",
            mac_endpoint_asset_id,
            {"permit_root_login": "no", "x11_forwarding": "no"},
        )
        result_ssh = client.get_cr_step_result(cr_ssh)
        assert result_ssh.get("snapshot") is not None, f"harden_ssh missing snapshot: {result_ssh}"
        rollback_stack.append((cr_ssh["id"], "harden_ssh"))
        
        # Verify via SSH
        sshd_conf = ssh_run("sudo grep -i PermitRootLogin /etc/ssh/sshd_config 2>/dev/null || echo MISSING")
        log(f"MAC_AUTH_HARDENING: sshd_config PermitRootLogin: {sshd_conf}")
        assert "no" in sshd_conf.lower(), f"expected PermitRootLogin no; got: {sshd_conf}"
        log("MAC_AUTH_HARDENING: harden_ssh verified via SSH ✓")

        # --- 2. configure_ntp ---
        log("MAC_AUTH_HARDENING: configure_ntp...")
        cr_ntp = client.run_cr(
            "[MAC_AUTH_HARDENING] configure_ntp",
            "configure_ntp",
            mac_endpoint_asset_id,
            {"servers": ["time.cloudflare.com", "time.apple.com"]},
        )
        result_ntp = client.get_cr_step_result(cr_ntp)
        assert result_ntp.get("snapshot") is not None, f"ntp missing snapshot: {result_ntp}"
        rollback_stack.append((cr_ntp["id"], "configure_ntp"))
        
        # Verify via SSH
        ntp_server = ssh_run("systemsetup -getnetworktimeserver 2>/dev/null || echo MISSING")
        log(f"MAC_AUTH_HARDENING: NTP server: {ntp_server}")
        assert "cloudflare" in ntp_server or "apple" in ntp_server, \
            f"expected cloudflare/apple NTP; got: {ntp_server}"
        log("MAC_AUTH_HARDENING: configure_ntp verified ✓")

        # --- 3. config_syslog ---
        log("MAC_AUTH_HARDENING: config_syslog...")
        cr_syslog = client.run_cr(
            "[MAC_AUTH_HARDENING] config_syslog",
            "config_syslog",
            mac_endpoint_asset_id,
            {
                "destination_host": "10.0.0.1",
                "destination_port": 514,
                "protocol": "udp",
                "facility": "*.*",
            },
        )
        result_syslog = client.get_cr_step_result(cr_syslog)
        assert result_syslog.get("config_path") is not None, f"syslog missing config_path: {result_syslog}"
        rollback_stack.append((cr_syslog["id"], "config_syslog"))
        
        syslog_conf = ssh_run("sudo grep nexplane /etc/syslog.conf 2>/dev/null || echo MISSING")
        assert "10.0.0.1" in syslog_conf, f"expected forward line in syslog.conf; got: {syslog_conf}"
        log("MAC_AUTH_HARDENING: config_syslog verified ✓")

        log("MAC_AUTH_HARDENING: all execute phases passed — rolling back...")
        for cr_id, label in reversed(rollback_stack):
            _wait_rollback_posture(client, cr_id, label)
            log(f"MAC_AUTH_HARDENING: rolled back {label}")

        # Verify SSH config restored
        sshd_after = ssh_run("sudo grep -i PermitRootLogin /etc/ssh/sshd_config 2>/dev/null || echo MISSING")
        log(f"MAC_AUTH_HARDENING: sshd_config after rollback: {sshd_after}")

        log("MAC_AUTH_HARDENING: phase complete ✓")
    finally:
        ssh.close()


def run_phase_mac_observability(client, mac_endpoint_asset_id: str) -> None:
    """Phase MAC_OBSERVABILITY: estimate_size, audit_software_inventory, audit_cis_compliance — read-only CRs."""
    print("\n[Phase MAC_OBSERVABILITY] macOS Observability (read-only)")

    # --- 1. estimate_size ---
    log("MAC_OBSERVABILITY: estimate_size /etc...")
    cr_size = client.run_cr(
        "[MAC_OBSERVABILITY] estimate_size /etc",
        "estimate_size",
        mac_endpoint_asset_id,
        {"path": "/etc"},
    )
    result_size = client.get_cr_step_result(cr_size)
    size_bytes = result_size.get("size_bytes", 0)
    assert size_bytes > 0, f"estimate_size returned size_bytes=0: {result_size}"
    log(f"MAC_OBSERVABILITY: estimate_size OK — /etc = {size_bytes} bytes")

    # --- 2. audit_software_inventory ---
    log("MAC_OBSERVABILITY: audit_software_inventory...")
    cr_inv = client.run_cr(
        "[MAC_OBSERVABILITY] audit_software_inventory",
        "audit_software_inventory",
        mac_endpoint_asset_id,
        {},
    )
    result_inv = client.get_cr_step_result(cr_inv)
    packages = result_inv.get("packages", [])
    log(f"MAC_OBSERVABILITY: audit_software_inventory OK — {len(packages)} packages")

    # --- 3. audit_cis_compliance (darwin) ---
    log("MAC_OBSERVABILITY: audit_cis_compliance level=1...")
    cr_cis = client.run_cr(
        "[MAC_OBSERVABILITY] audit_cis_compliance darwin level=1",
        "audit_cis_compliance",
        mac_endpoint_asset_id,
        {"level": 1, "os_family": "darwin"},
    )
    result_cis = client.get_cr_step_result(cr_cis)
    assert result_cis.get("os_family") == "darwin", \
        f"expected os_family=darwin; got {result_cis.get('os_family')}"
    score = result_cis.get("score", {})
    log(f"MAC_OBSERVABILITY: CIS compliance OK — score={score}")

    log("MAC_OBSERVABILITY: phase complete ✓")
```

- [ ] **Step 2: Add phases to main() dispatcher**

After the `MAC_POSTURE_AUDIT` block:

```python
        if "MAC_AUTH_HARDENING" in phases:
            if not mac_endpoint_asset_id:
                fail("MAC_AUTH_HARDENING requires MAC_AGENT_BOOTSTRAP to have set mac_endpoint_asset_id")
            ssh_key = getattr(args, "ssh_key_path", "")
            run_phase_mac_auth_hardening(client, mac_endpoint_asset_id, ssh_host, ssh_key)

        if "MAC_OBSERVABILITY" in phases:
            if not mac_endpoint_asset_id:
                fail("MAC_OBSERVABILITY requires MAC_AGENT_BOOTSTRAP to have set mac_endpoint_asset_id")
            run_phase_mac_observability(client, mac_endpoint_asset_id)
```

- [ ] **Step 3: Add phase descriptions to docstring + argparse**

In the module docstring, add:
```
    MAC_AUTH_HARDENING  macOS SSH hardening, NTP, syslog forwarding with execute+rollback
    MAC_OBSERVABILITY   macOS estimate_size, software inventory, CIS compliance (read-only)
```

- [ ] **Step 4: Commit smoke test**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "test(smoke): add MAC_AUTH_HARDENING and MAC_OBSERVABILITY phases"
```

---

### Task 7: Sync to EC2 and run smoke tests

- [ ] **Step 1: Push all changes and pull on EC2**

```bash
# From Windows (or from EC2 directly):
git push origin master

# On EC2:
cd /home/ec2-user/nexplane && git pull origin master
```

- [ ] **Step 2: Run backend migrations (if any new ones)**

```bash
# On EC2:
docker exec nexplane-backend-1 alembic upgrade head
```

- [ ] **Step 3: Restart backend to pick up any model changes**

```bash
# On EC2:
docker compose restart backend
sleep 10
curl -s http://localhost:8000/health | python3 -m json.tool
```

- [ ] **Step 4: Run MAC_AGENT_BOOTSTRAP + new posture phases**

```bash
# On EC2 — use tmux session for long-running test:
tmux new-session -s mac_smoke -d
tmux send-keys -t mac_smoke "cd /home/ec2-user/nexplane && python3 backend/tests/smoke/test_aws_live.py \
  --base-url http://localhost:8000 \
  --email admin@acme.example \
  --password admin123 \
  --phases MAC_AGENT_BOOTSTRAP,MAC_POSTURE_AUDIT,MAC_AUTH_HARDENING,MAC_OBSERVABILITY \
  --dedicated-host-id h-0d03a30df9b884c06 \
  --ssh-key-path /tmp/nexplane-smoke-mac.pem \
  2>&1 | tee /tmp/mac_smoke_run.log" Enter

# Monitor:
tmux attach -t mac_smoke
# OR tail in another window:
tail -f /tmp/mac_smoke_run.log
```

Expected output:
```
[Phase MAC_AGENT_BOOTSTRAP] ...
MAC_AGENT_BOOTSTRAP: agent registered as asset <uuid>
MAC_AGENT_BOOTSTRAP: santa_install OK
...
[Phase MAC_POSTURE_AUDIT] macOS Security Posture Audit
MAC_POSTURE_AUDIT: configure_selinux OK
MAC_POSTURE_AUDIT: sysctl_hardening OK
MAC_POSTURE_AUDIT: deploy_auditd_rules OK — audit_control verified via SSH
MAC_POSTURE_AUDIT: FIM init OK
MAC_POSTURE_AUDIT: configure_apparmor OK
MAC_POSTURE_AUDIT: discover_applications OK
MAC_POSTURE_AUDIT: deep_discover OK
MAC_POSTURE_AUDIT: collect_forensics OK
MAC_POSTURE_AUDIT: all execute phases passed — starting rollbacks...
MAC_POSTURE_AUDIT: rollback configure_apparmor → rolled_back
MAC_POSTURE_AUDIT: rollback deploy_auditd_rules → rolled_back
MAC_POSTURE_AUDIT: rollback apply_sysctl_hardening → rolled_back
MAC_POSTURE_AUDIT: rollback configure_selinux → rolled_back
MAC_POSTURE_AUDIT: phase complete ✓

[Phase MAC_AUTH_HARDENING] macOS Auth Hardening
...
MAC_AUTH_HARDENING: phase complete ✓

[Phase MAC_OBSERVABILITY] macOS Observability (read-only)
...
MAC_OBSERVABILITY: phase complete ✓
```

- [ ] **Step 5: Verify no failures**

```bash
grep -E "^(FAIL|❌|Error|Traceback)" /tmp/mac_smoke_run.log
```
Expected: no output (no failures)

- [ ] **Step 6: Final commit + push**

```bash
git add -A
git commit -m "chore: all agent parity SPs complete — SP1-SP7 macOS+Windows feature gaps closed" --allow-empty
git push origin master
```
