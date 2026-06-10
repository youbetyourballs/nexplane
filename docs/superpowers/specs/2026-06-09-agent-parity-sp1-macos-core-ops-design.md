# Agent Parity SP1: macOS Core Ops (change_ip + reboot)

**Goal:** Implement real `change_ip` and `reboot` on macOS so the rollback guarantee holds on darwin — these are the two commands the platform depends on for safe network reconfiguration and recovery.

**Architecture:** Mirror the Linux pattern exactly: `_darwin.go` with real implementation, narrow `_other.go` tag to `!linux && !darwin`. macOS uses `networksetup` CLI for all IP/DNS config (same role as `nmcli` on Linux). Dead man's switch logic (`changip_deadman.go`) is already platform-agnostic — darwin just calls `startDeadManSwitch` the same way Linux does.

**Tech Stack:** Go, `networksetup` CLI, `/sbin/shutdown`, `launchctl list`, existing `changip` package helpers.

**No-equivalent items:** None — both commands have full macOS native equivalents.

---

## File Map

**Create:**
- `agent/commands/changip/changip_darwin.go` — full IP/DNS/snapshot/rollback via `networksetup`
- `agent/commands/reboot/reboot_darwin.go` — `shutdown -r +N`, `launchctl list` for service verify

**Modify:**
- `agent/commands/changip/changip_other.go` — narrow build tag to `//go:build !linux && !darwin`
- `agent/commands/reboot/reboot_other.go` (create if missing) — `//go:build !linux && !darwin && !windows` stubs

---

## Task 1: changip macOS implementation

**Files:**
- Create: `agent/commands/changip/changip_darwin.go`
- Modify: `agent/commands/changip/changip_other.go`

- [ ] **Step 1: Read existing files**

Read `agent/commands/changip/changip_linux.go`, `changip_darwin.go`, `changip_other.go`, `changip_deadman.go`, `changip.go` in full before writing anything.

- [ ] **Step 2: Narrow the other.go build tag**

In `agent/commands/changip/changip_other.go`, change:
```go
//go:build !linux
```
to:
```go
//go:build !linux && !darwin
```

- [ ] **Step 3: Create changip_darwin.go**

```go
//go:build darwin

package changip

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

// DarwinSnapshot captures full network state for one interface using networksetup.
type DarwinSnapshot struct {
	Interface    string   `json:"interface"`
	ServiceName  string   `json:"service_name"` // e.g. "Wi-Fi", "Ethernet"
	Mode         string   `json:"mode"`         // "dhcp" or "static"
	IPv4         string   `json:"ipv4"`
	Subnet       string   `json:"subnet"`
	GatewayV4    string   `json:"gateway_v4"`
	DNSServers   []string `json:"dns_servers"`
	SearchDomains []string `json:"search_domains"`
}

func executeOS(params map[string]any) (map[string]any, error) {
	iface, _ := params["interface"].(string)
	mode, _ := params["mode"].(string)
	if iface == "" {
		return nil, fmt.Errorf("interface is required")
	}
	if mode != "static" && mode != "dhcp" {
		return nil, fmt.Errorf("mode must be 'static' or 'dhcp', got %q", mode)
	}

	svc, err := serviceForInterface(iface)
	if err != nil {
		return nil, fmt.Errorf("cannot find networksetup service for %s: %w", iface, err)
	}

	snap, err := captureDarwinSnapshot(iface, svc)
	if err != nil {
		return nil, fmt.Errorf("capturing snapshot: %w", err)
	}
	snapBytes, _ := json.Marshal(snap)
	var snapMap map[string]any
	json.Unmarshal(snapBytes, &snapMap)

	method, _ := params["method"].(string)
	if method == "" {
		method = "auto"
	}
	if method == "auto" {
		tsIP, tsActive := IsTailscaleActive()
		probeURL, _ := params["probe_url"].(string)
		if probeURL == "" {
			probeURL = os.Getenv("NP_CONTROL_PLANE")
		}
		if tsActive && probeURL != "" && IsControlPlaneReachableViaTailscale(probeURL, 5*time.Second) {
			_ = tsIP
			method = "tailscale"
		} else {
			method = "commit_timer"
		}
	}

	commitTimerSecs := paramIntDarwin(params, "commit_timer_seconds", 30)
	if commitTimerSecs < 10 {
		commitTimerSecs = 10
	}
	probeURL, _ := params["probe_url"].(string)
	if probeURL == "" {
		probeURL = os.Getenv("NP_CONTROL_PLANE")
	}
	probeIntervalSecs := paramIntDarwin(params, "probe_interval_seconds", 5)

	result := map[string]any{
		"action":      "change_ip",
		"interface":   iface,
		"mode":        mode,
		"applied":     false,
		"snapshot":    snapMap,
		"applied_at":  time.Now().UTC().Format(time.RFC3339),
		"method_used": method,
	}

	switch method {
	case "tailscale":
		if err := applyDarwinIPChange(svc, mode, params); err != nil {
			return nil, err
		}
		if err := applyDarwinDNS(svc, params); err != nil {
			return nil, err
		}
		result["applied"] = true

	case "commit_timer":
		pr := PendingRollback{
			JobID:             fmt.Sprintf("job-%d", time.Now().UnixNano()),
			ExpiresAt:         time.Now().Add(time.Duration(commitTimerSecs) * time.Second),
			RollbackParams:    snapMap,
			ProbeURL:          probeURL,
			ProbeIntervalSecs: probeIntervalSecs,
			ProbeTimeoutSecs:  3,
		}
		path := activePendingRollbackPath()
		_, err := startDeadManSwitch(pr, path, func() {
			_, _ = rollbackOS(map[string]any{"snapshot": snapMap, "interface": iface})
		})
		if err != nil {
			return nil, fmt.Errorf("start dead man's switch: %w", err)
		}
		if err := applyDarwinIPChange(svc, mode, params); err != nil {
			return nil, err
		}
		if err := applyDarwinDNS(svc, params); err != nil {
			return nil, err
		}
		result["applied"] = true
		result["commit_timer_started"] = true

	case "manual":
		if err := applyDarwinIPChange(svc, mode, params); err != nil {
			return nil, err
		}
		result["applied"] = true
		result["requires_confirmation"] = true

	default:
		if err := applyDarwinIPChange(svc, mode, params); err != nil {
			return nil, err
		}
		if err := applyDarwinDNS(svc, params); err != nil {
			return nil, err
		}
		result["applied"] = true
	}

	return result, nil
}

func rollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(map[string]any)
	if !ok || snapshot == nil {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	iface, _ := snapshot["interface"].(string)
	svc, _ := snapshot["service_name"].(string)
	if iface == "" || svc == "" {
		return nil, fmt.Errorf("snapshot missing interface or service_name")
	}

	mode, _ := snapshot["mode"].(string)
	if mode == "" {
		mode = "dhcp"
	}

	rollParams := map[string]any{
		"new_ip_v4":     snapshot["ipv4"],
		"new_subnet":    snapshot["subnet"],
		"new_gateway_v4": snapshot["gateway_v4"],
	}

	if err := applyDarwinIPChange(svc, mode, rollParams); err != nil {
		return nil, err
	}

	// Restore DNS
	if servers, ok := snapshot["dns_servers"].([]any); ok && len(servers) > 0 {
		parts := make([]string, 0, len(servers))
		for _, s := range servers {
			if str, ok := s.(string); ok {
				parts = append(parts, str)
			}
		}
		_ = runNS("networksetup", "-setdnsservers", svc, strings.Join(parts, " "))
	} else {
		_ = runNS("networksetup", "-setdnsservers", svc, "empty")
	}

	return map[string]any{"rolled_back": true}, nil
}

// serviceForInterface finds the networksetup service name for a BSD interface (e.g. "en0" → "Wi-Fi").
func serviceForInterface(iface string) (string, error) {
	out, err := exec.Command("networksetup", "-listallhardwareports").Output()
	if err != nil {
		return "", err
	}
	lines := strings.Split(string(out), "\n")
	var lastService string
	for _, line := range lines {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "Hardware Port:") {
			lastService = strings.TrimSpace(strings.TrimPrefix(line, "Hardware Port:"))
		}
		if strings.HasPrefix(line, "Device:") {
			dev := strings.TrimSpace(strings.TrimPrefix(line, "Device:"))
			if dev == iface {
				return lastService, nil
			}
		}
	}
	return "", fmt.Errorf("no networksetup service found for interface %q", iface)
}

func captureDarwinSnapshot(iface, svc string) (*DarwinSnapshot, error) {
	snap := &DarwinSnapshot{Interface: iface, ServiceName: svc}
	out, err := exec.Command("networksetup", "-getinfo", svc).Output()
	if err != nil {
		return snap, nil
	}
	for _, line := range strings.Split(string(out), "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "IP address:") {
			snap.IPv4 = strings.TrimSpace(strings.TrimPrefix(line, "IP address:"))
		} else if strings.HasPrefix(line, "Subnet mask:") {
			snap.Subnet = strings.TrimSpace(strings.TrimPrefix(line, "Subnet mask:"))
		} else if strings.HasPrefix(line, "Router:") {
			snap.GatewayV4 = strings.TrimSpace(strings.TrimPrefix(line, "Router:"))
		} else if strings.HasPrefix(line, "IPv4 Configured Via:") {
			via := strings.TrimSpace(strings.TrimPrefix(line, "IPv4 Configured Via:"))
			if strings.EqualFold(via, "DHCP") {
				snap.Mode = "dhcp"
			} else {
				snap.Mode = "static"
			}
		}
	}
	// DNS
	dnsOut, _ := exec.Command("networksetup", "-getdnsservers", svc).Output()
	for _, line := range strings.Split(string(dnsOut), "\n") {
		line = strings.TrimSpace(line)
		if line != "" && !strings.Contains(line, "There aren't") {
			snap.DNSServers = append(snap.DNSServers, line)
		}
	}
	return snap, nil
}

func applyDarwinIPChange(svc, mode string, params map[string]any) error {
	if mode == "dhcp" {
		return runNS("networksetup", "-setdhcp", svc)
	}
	ipv4, _ := params["new_ip_v4"].(string)
	subnet, _ := params["new_subnet"].(string)
	if subnet == "" {
		subnet = "255.255.255.0"
	}
	gw, _ := params["new_gateway_v4"].(string)
	if ipv4 == "" {
		return fmt.Errorf("new_ip_v4 required for static mode")
	}
	args := []string{"-setmanual", svc, ipv4, subnet}
	if gw != "" {
		args = append(args, gw)
	}
	return runNS("networksetup", args...)
}

func applyDarwinDNS(svc string, params map[string]any) error {
	var servers []string
	if raw, ok := params["dns_servers"].([]any); ok {
		for _, v := range raw {
			if s, ok := v.(string); ok {
				servers = append(servers, s)
			}
		}
	}
	if len(servers) == 0 {
		return nil
	}
	args := append([]string{"-setdnsservers", svc}, servers...)
	return runNS("networksetup", args...)
}

func runNS(name string, args ...string) error {
	out, err := exec.Command(name, args...).CombinedOutput()
	if err != nil {
		return fmt.Errorf("%s %v: %w (output: %s)", name, args, err, out)
	}
	return nil
}

func paramIntDarwin(params map[string]any, key string, def int) int {
	switch v := params[key].(type) {
	case int:
		return v
	case float64:
		return int(v)
	case int64:
		return int(v)
	}
	return def
}
```

- [ ] **Step 4: Build darwin agent to verify compilation**

```bash
cd agent
GOOS=darwin GOARCH=arm64 go build ./...
```
Expected: no errors.

- [ ] **Step 5: Write unit tests**

Create `agent/commands/changip/changip_darwin_test.go`:
```go
//go:build darwin

package changip

import (
	"testing"
)

func TestServiceForInterface_NotFound(t *testing.T) {
	_, err := serviceForInterface("nonexistent999")
	if err == nil {
		t.Fatal("expected error for unknown interface")
	}
}

func TestCaptureDarwinSnapshot_Runs(t *testing.T) {
	// Just verify it doesn't panic — real interface needed for full test
	snap, err := captureDarwinSnapshot("lo0", "Loopback")
	if err != nil {
		t.Logf("captureDarwinSnapshot: %v (ok if no loopback service)", err)
	}
	if snap == nil {
		t.Fatal("expected non-nil snapshot")
	}
}

func TestRollbackOS_MissingSnapshot(t *testing.T) {
	_, err := rollbackOS(map[string]any{})
	if err == nil {
		t.Fatal("expected error with no snapshot")
	}
}
```

Run: `GOOS=darwin go test ./commands/changip/ -run TestServiceForInterface_NotFound -v`
Expected: PASS (no exec needed for not-found test).

- [ ] **Step 6: Commit**

```bash
git add agent/commands/changip/changip_darwin.go agent/commands/changip/changip_other.go agent/commands/changip/changip_darwin_test.go
git commit -m "feat(agent/darwin): implement change_ip via networksetup with dead man's switch"
```

---

## Task 2: reboot macOS implementation

**Files:**
- Create: `agent/commands/reboot/reboot_darwin.go`
- Create (if missing): `agent/commands/reboot/reboot_other.go`

- [ ] **Step 1: Read existing files**

Read `agent/commands/reboot/reboot.go`, `reboot_linux.go`, `reboot_windows.go`, `reboot_test.go` in full.

- [ ] **Step 2: Create reboot_darwin.go**

```go
//go:build darwin

package reboot

import (
	"fmt"
	"os/exec"
	"strings"
)

func executeOS(params map[string]any) (map[string]any, error) {
	delayMinutes := 0
	if v, ok := params["delay_minutes"].(float64); ok {
		delayMinutes = int(v)
	}
	if delayMinutes < 0 {
		delayMinutes = 0
	}

	// macOS shutdown -r takes minutes; +0 means now (after 1s grace)
	delay := fmt.Sprintf("+%d", delayMinutes)
	out, err := exec.Command("shutdown", "-r", delay).CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("shutdown -r %s: %w (output: %s)", delay, err, out)
	}
	return map[string]any{
		"action":        "reboot",
		"delay_minutes": delayMinutes,
		"scheduled":     true,
		"output":        strings.TrimSpace(string(out)),
	}, nil
}

func verifyPostRebootOS(params map[string]any) (map[string]any, error) {
	service, _ := params["service"].(string)
	if service == "" {
		return map[string]any{"verified": true, "note": "no service specified"}, nil
	}
	// launchctl list exits 0 if service is loaded, non-zero otherwise
	out, err := exec.Command("launchctl", "list", service).CombinedOutput()
	running := err == nil && !strings.Contains(string(out), "Could not find service")
	return map[string]any{
		"verified": running,
		"service":  service,
		"output":   strings.TrimSpace(string(out)),
	}, nil
}
```

- [ ] **Step 3: Create reboot_other.go if it doesn't exist**

```go
//go:build !linux && !darwin && !windows

package reboot

import (
	"fmt"
	"runtime"
)

func executeOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("reboot not supported on %s", runtime.GOOS)
}

func verifyPostRebootOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("reboot not supported on %s", runtime.GOOS)
}
```

- [ ] **Step 4: Build and test**

```bash
cd agent
GOOS=darwin GOARCH=arm64 go build ./...
GOOS=linux go build ./...
GOOS=windows go build ./...
```
Expected: all three compile cleanly.

- [ ] **Step 5: Commit**

```bash
git add agent/commands/reboot/reboot_darwin.go agent/commands/reboot/reboot_other.go
git commit -m "feat(agent/darwin): implement reboot via shutdown -r; add missing reboot_other.go"
```
