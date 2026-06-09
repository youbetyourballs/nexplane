# Agent Parity SP1+SP2: macOS change_ip, reboot, isolation, firewall

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement macOS change_ip (networksetup), reboot (shutdown -r), host isolation (pfctl), and application firewall (socketfilterfw) in the Nexplane agent.

**Architecture:** Each OS-specific file implements the `executeOS`/`rollbackOS` function pair. isolation_darwin.go adds `PFRules` to `PreIsolationState`. Build tags on `_other.go` files are widened to exclude darwin once real implementations exist.

**Tech Stack:** Go 1.21, macOS `networksetup` CLI, `pfctl`, `/usr/libexec/ApplicationFirewall/socketfilterfw`, `shutdown -r`

**Context:** Code lives in `agent/` directory. Sync to EC2 at 100.101.186.39 with `scp -i ~/.ssh/id_ed25519 -r agent/ ec2-user@100.101.186.39:/home/ec2-user/nexplane/agent/` before building. All edits happen on Windows; nothing runs locally.

---

### Task 1: changip_darwin.go

**Files:**
- Create: `agent/commands/changip/changip_darwin.go`
- Create: `agent/commands/changip/changip_darwin_test.go`

- [ ] **Step 1: Write the failing test**

Create `agent/commands/changip/changip_darwin_test.go`:

```go
//go:build darwin

package changip

import (
	"encoding/json"
	"os/exec"
	"strings"
	"testing"
)

func TestExecuteOS_DarwinAutoMode(t *testing.T) {
	var cmds []string
	execCommand = func(name string, args ...string) *exec.Cmd {
		cmds = append(cmds, name+" "+strings.Join(args, " "))
		return exec.Command("echo", "ok")
	}
	t.Cleanup(func() { execCommand = exec.Command })

	params := map[string]any{
		"interface": "en0",
		"mode":      "auto",
	}
	result, err := executeOS(params)
	if err != nil {
		t.Fatalf("executeOS: %v", err)
	}
	if result["snapshot"] == nil {
		t.Fatal("snapshot missing from result")
	}
	found := false
	for _, c := range cmds {
		if strings.Contains(c, "networksetup") && strings.Contains(c, "setdhcp") {
			found = true
		}
	}
	if !found {
		t.Fatalf("expected networksetup -setdhcp call; got: %v", cmds)
	}
}

func TestExecuteOS_DarwinManualMode(t *testing.T) {
	var cmds []string
	execCommand = func(name string, args ...string) *exec.Cmd {
		cmds = append(cmds, name+" "+strings.Join(args, " "))
		return exec.Command("echo", "192.168.1.100")
	}
	t.Cleanup(func() { execCommand = exec.Command })

	params := map[string]any{
		"interface":      "en0",
		"mode":           "manual",
		"ip_address":     "10.0.0.50",
		"subnet_mask":    "255.255.255.0",
		"gateway":        "10.0.0.1",
		"dns_servers":    []any{"8.8.8.8"},
	}
	result, err := executeOS(params)
	if err != nil {
		t.Fatalf("executeOS: %v", err)
	}
	_ = result
	found := false
	for _, c := range cmds {
		if strings.Contains(c, "setmanual") {
			found = true
		}
	}
	if !found {
		t.Fatalf("expected networksetup -setmanual; got: %v", cmds)
	}
}

func TestRollbackOS_Darwin(t *testing.T) {
	snap := DarwinSnapshot{
		Interface:   "en0",
		ServiceName: "Wi-Fi",
		Mode:        "auto",
		IPv4:        "192.168.1.5",
		Subnet:      "255.255.255.0",
		Gateway:     "192.168.1.1",
		DNS:         []string{"8.8.8.8"},
	}
	b, _ := json.Marshal(snap)

	var cmds []string
	execCommand = func(name string, args ...string) *exec.Cmd {
		cmds = append(cmds, name+" "+strings.Join(args, " "))
		return exec.Command("echo", "ok")
	}
	t.Cleanup(func() { execCommand = exec.Command })

	result, err := rollbackOS(map[string]any{"snapshot": string(b)})
	if err != nil {
		t.Fatalf("rollbackOS: %v", err)
	}
	if result["rolled_back"] != true {
		t.Fatal("expected rolled_back=true")
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd agent && go test ./commands/changip/ -run TestExecuteOS_Darwin -v 2>&1 | head -20
```
Expected: compile error or "not defined"

- [ ] **Step 3: Implement changip_darwin.go**

Create `agent/commands/changip/changip_darwin.go`:

```go
//go:build darwin

package changip

import (
	"encoding/json"
	"fmt"
	"os/exec"
	"strings"
	"time"
)

// DarwinSnapshot holds the pre-change network state for rollback.
type DarwinSnapshot struct {
	Interface   string   `json:"interface"`
	ServiceName string   `json:"service_name"`
	Mode        string   `json:"mode"` // "auto" or "manual"
	IPv4        string   `json:"ipv4"`
	Subnet      string   `json:"subnet"`
	Gateway     string   `json:"gateway"`
	DNS         []string `json:"dns"`
}

func executeOS(params map[string]any) (map[string]any, error) {
	iface, _ := params["interface"].(string)
	if iface == "" {
		iface = "en0"
	}
	mode, _ := params["mode"].(string)
	if mode == "" {
		mode = "auto"
	}

	snap, err := captureDarwinSnapshot(iface)
	if err != nil {
		return nil, fmt.Errorf("snapshot: %w", err)
	}

	switch mode {
	case "auto", "dhcp":
		if err := applyDHCP(snap.ServiceName); err != nil {
			return nil, err
		}
	case "manual":
		ip, _ := params["ip_address"].(string)
		subnet, _ := params["subnet_mask"].(string)
		gw, _ := params["gateway"].(string)
		if ip == "" {
			return nil, fmt.Errorf("ip_address required for manual mode")
		}
		if subnet == "" {
			subnet = "255.255.255.0"
		}
		if err := applyDarwinIPChange(snap.ServiceName, ip, subnet, gw); err != nil {
			return nil, err
		}
		if dnsRaw, ok := params["dns_servers"].([]any); ok {
			dns := make([]string, 0, len(dnsRaw))
			for _, d := range dnsRaw {
				if s, ok := d.(string); ok {
					dns = append(dns, s)
				}
			}
			if len(dns) > 0 {
				if err := applyDarwinDNS(snap.ServiceName, dns); err != nil {
					return nil, err
				}
			}
		}
	case "tailscale":
		// Tailscale manages its own interface (utun*); nothing to configure via networksetup
	default:
		return nil, fmt.Errorf("unsupported mode %q: must be auto, manual, or tailscale", mode)
	}

	snapJSON, _ := json.Marshal(snap)
	return map[string]any{
		"interface":    iface,
		"service_name": snap.ServiceName,
		"mode":         mode,
		"snapshot":     string(snapJSON),
		"applied_at":   time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func rollbackOS(params map[string]any) (map[string]any, error) {
	snapStr, ok := params["snapshot"].(string)
	if !ok || snapStr == "" {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	var snap DarwinSnapshot
	if err := json.Unmarshal([]byte(snapStr), &snap); err != nil {
		return nil, fmt.Errorf("parse snapshot: %w", err)
	}
	if snap.ServiceName == "" {
		svc, err := serviceForInterface(snap.Interface)
		if err != nil {
			return nil, fmt.Errorf("serviceForInterface: %w", err)
		}
		snap.ServiceName = svc
	}

	switch snap.Mode {
	case "auto", "dhcp", "":
		if err := applyDHCP(snap.ServiceName); err != nil {
			return nil, err
		}
	default:
		if err := applyDarwinIPChange(snap.ServiceName, snap.IPv4, snap.Subnet, snap.Gateway); err != nil {
			return nil, err
		}
	}
	if len(snap.DNS) > 0 {
		if err := applyDarwinDNS(snap.ServiceName, snap.DNS); err != nil {
			return nil, err
		}
	}
	return map[string]any{"rolled_back": true, "interface": snap.Interface}, nil
}

// serviceForInterface maps a BSD interface name (e.g. "en0") to its
// networksetup service name (e.g. "Wi-Fi").
func serviceForInterface(iface string) (string, error) {
	out, err := runNS("-listallhardwareports")
	if err != nil {
		return "", err
	}
	lines := strings.Split(out, "\n")
	var lastName string
	for _, l := range lines {
		l = strings.TrimSpace(l)
		if strings.HasPrefix(l, "Hardware Port:") {
			lastName = strings.TrimPrefix(l, "Hardware Port:")
			lastName = strings.TrimSpace(lastName)
		}
		if strings.HasPrefix(l, "Device:") {
			dev := strings.TrimSpace(strings.TrimPrefix(l, "Device:"))
			if dev == iface {
				return lastName, nil
			}
		}
	}
	// Fall back: return interface name as-is
	return iface, nil
}

func captureDarwinSnapshot(iface string) (*DarwinSnapshot, error) {
	svc, err := serviceForInterface(iface)
	if err != nil {
		return nil, err
	}
	snap := &DarwinSnapshot{Interface: iface, ServiceName: svc, Mode: "manual"}

	// Detect DHCP
	out, _ := runNS("-getinfo", svc)
	if strings.Contains(out, "DHCP Configuration") || strings.Contains(out, "Manual Configuration") {
		if strings.Contains(out, "DHCP") {
			snap.Mode = "auto"
		}
	}
	for _, line := range strings.Split(out, "\n") {
		line = strings.TrimSpace(line)
		switch {
		case strings.HasPrefix(line, "IP address:"):
			snap.IPv4 = strings.TrimSpace(strings.TrimPrefix(line, "IP address:"))
		case strings.HasPrefix(line, "Subnet mask:"):
			snap.Subnet = strings.TrimSpace(strings.TrimPrefix(line, "Subnet mask:"))
		case strings.HasPrefix(line, "Router:"):
			snap.Gateway = strings.TrimSpace(strings.TrimPrefix(line, "Router:"))
		}
	}

	dnsOut, _ := runNS("-getdnsservers", svc)
	for _, l := range strings.Split(dnsOut, "\n") {
		l = strings.TrimSpace(l)
		if l != "" && !strings.Contains(l, "There aren't") {
			snap.DNS = append(snap.DNS, l)
		}
	}
	return snap, nil
}

func applyDHCP(svc string) error {
	_, err := runNS("-setdhcp", svc)
	return err
}

func applyDarwinIPChange(svc, ip, subnet, gw string) error {
	_, err := runNS("-setmanual", svc, ip, subnet, gw)
	return err
}

func applyDarwinDNS(svc string, dns []string) error {
	args := append([]string{"-setdnsservers", svc}, dns...)
	_, err := runNS(args...)
	return err
}

func runNS(args ...string) (string, error) {
	cmd := execCommand("networksetup", args...)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return "", fmt.Errorf("networksetup %v: %w (output: %s)", args, err, out)
	}
	return string(out), nil
}
```

- [ ] **Step 4: Run tests**

```bash
cd agent && go test ./commands/changip/ -run TestExecuteOS_Darwin -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent/commands/changip/changip_darwin.go agent/commands/changip/changip_darwin_test.go
git commit -m "feat(agent): implement macOS change_ip via networksetup"
```

---

### Task 2: reboot_darwin.go + reboot_other.go stub

**Files:**
- Create: `agent/commands/reboot/reboot_darwin.go`
- Create: `agent/commands/reboot/reboot_darwin_test.go`

Note: `reboot_other.go` does not exist yet — the build tag on `reboot_windows.go` is `//go:build windows` which is already specific, so no `_other.go` file is needed. Darwin needs its own file.

- [ ] **Step 1: Write failing test**

Create `agent/commands/reboot/reboot_darwin_test.go`:

```go
//go:build darwin

package reboot

import (
	"os/exec"
	"strings"
	"testing"
)

func TestExecuteOS_DarwinDryRun(t *testing.T) {
	result, err := executeOS(map[string]any{"dry_run": true})
	if err != nil {
		t.Fatalf("dry run failed: %v", err)
	}
	if result["dry_run"] != true {
		t.Fatal("expected dry_run=true")
	}
	if result["graceful_delay_seconds"] == nil {
		t.Fatal("expected graceful_delay_seconds")
	}
}

func TestExecuteOS_DarwinSchedules(t *testing.T) {
	var called []string
	execCommandDarwin = func(name string, args ...string) *exec.Cmd {
		called = append(called, name+" "+strings.Join(args, " "))
		return exec.Command("echo", "ok")
	}
	t.Cleanup(func() { execCommandDarwin = exec.Command })

	result, err := executeOS(map[string]any{"graceful_delay_seconds": float64(120)})
	if err != nil {
		t.Fatalf("executeOS: %v", err)
	}
	_ = result
	found := false
	for _, c := range called {
		if strings.Contains(c, "shutdown") && strings.Contains(c, "-r") {
			found = true
		}
	}
	if !found {
		t.Fatalf("expected shutdown -r; got: %v", called)
	}
}

func TestVerifyPostRebootOS_Darwin(t *testing.T) {
	execCommandDarwin = func(name string, args ...string) *exec.Cmd {
		return exec.Command("echo", "com.apple.Finder")
	}
	t.Cleanup(func() { execCommandDarwin = exec.Command })

	result, err := verifyPostRebootOS(map[string]any{
		"verify_services": []any{"com.apple.Finder"},
	})
	if err != nil {
		t.Fatalf("verifyPostRebootOS: %v", err)
	}
	results, _ := result["service_results"].(map[string]string)
	if results["com.apple.Finder"] == "" {
		t.Fatal("expected service result")
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd agent && go test ./commands/reboot/ -run TestExecuteOS_Darwin -v 2>&1 | head -10
```
Expected: compile error

- [ ] **Step 3: Implement reboot_darwin.go**

Create `agent/commands/reboot/reboot_darwin.go`:

```go
//go:build darwin

package reboot

import (
	"context"
	"fmt"
	"os/exec"
	"strings"
	"time"
)

// execCommandDarwin is mockable in tests.
var execCommandDarwin = exec.Command

func executeOS(params map[string]any) (map[string]any, error) {
	delay := 60
	if d, ok := params["graceful_delay_seconds"].(float64); ok && int(d) > 60 {
		delay = int(d)
	}
	dryRun, _ := params["dry_run"].(bool)

	if !dryRun {
		// macOS shutdown -r uses +N minutes; round up to nearest minute
		delayMin := fmt.Sprintf("+%d", (delay+59)/60)
		cmd := execCommandDarwin("shutdown", "-r", delayMin, "Nexplane scheduled reboot")
		if out, err := cmd.CombinedOutput(); err != nil {
			return nil, fmt.Errorf("shutdown failed: %w (output: %s)", err, out)
		}
	}

	return map[string]any{
		"action":                 "graceful_reboot",
		"graceful_delay_seconds": delay,
		"scheduled_at":           time.Now().UTC().Format(time.RFC3339),
		"dry_run":                dryRun,
	}, nil
}

func verifyPostRebootOS(params map[string]any) (map[string]any, error) {
	rawSvcs, _ := params["verify_services"].([]any)
	var services []string
	for _, s := range rawSvcs {
		if sv, ok := s.(string); ok && sv != "" {
			services = append(services, sv)
		}
	}

	results := map[string]string{}
	for _, svc := range services {
		// launchctl list <label> exits non-zero if not running
		out, err := execCommandDarwin(
			"launchctl", "list", svc,
		).Output()
		ctx := context.Background()
		_ = ctx
		if err != nil {
			results[svc] = "not-running"
		} else {
			output := strings.TrimSpace(string(out))
			if strings.Contains(output, svc) {
				results[svc] = "running"
			} else {
				results[svc] = "unknown"
			}
		}
	}

	return map[string]any{
		"service_results": results,
		"verified_at":     time.Now().UTC().Format(time.RFC3339),
	}, nil
}
```

- [ ] **Step 4: Run tests**

```bash
cd agent && go test ./commands/reboot/ -run TestExecuteOS_Darwin -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent/commands/reboot/reboot_darwin.go agent/commands/reboot/reboot_darwin_test.go
git commit -m "feat(agent): implement macOS reboot via shutdown -r"
```

---

### Task 3: isolation_darwin.go + PreIsolationState.PFRules

**Files:**
- Modify: `agent/commands/isolation/isolation.go` — add `PFRules string` to struct + serialization
- Create: `agent/commands/isolation/isolation_darwin.go`
- Create: `agent/commands/isolation/isolation_darwin_test.go`
- Modify: `agent/commands/isolation/isolation_other.go` — build tag add `&& !darwin`

- [ ] **Step 1: Write failing test**

Create `agent/commands/isolation/isolation_darwin_test.go`:

```go
//go:build darwin

package isolation

import (
	"context"
	"os/exec"
	"strings"
	"testing"
)

func TestIsolateDarwin_CapturesAndAppliesRules(t *testing.T) {
	var cmds []string
	execCommandIsolation = func(name string, args ...string) *exec.Cmd {
		cmds = append(cmds, name+" "+strings.Join(args, " "))
		return exec.Command("echo", "pass in all\npass out all")
	}
	t.Cleanup(func() { execCommandIsolation = exec.Command })

	cfg := IsolationConfig{
		ManagementCIDR:  "10.0.0.0/8",
		ControlPlaneURL: "https://10.0.1.50",
	}
	state, err := isolateOS(context.Background(), cfg)
	if err != nil {
		t.Fatalf("isolateOS: %v", err)
	}
	if state.OS != "darwin" {
		t.Fatalf("expected OS=darwin, got %q", state.OS)
	}
	foundPfctl := false
	for _, c := range cmds {
		if strings.Contains(c, "pfctl") {
			foundPfctl = true
		}
	}
	if !foundPfctl {
		t.Fatalf("expected pfctl call; got: %v", cmds)
	}
}

func TestRestoreDarwin_RestoresRules(t *testing.T) {
	var cmds []string
	execCommandIsolation = func(name string, args ...string) *exec.Cmd {
		cmds = append(cmds, name+" "+strings.Join(args, " "))
		return exec.Command("echo", "ok")
	}
	t.Cleanup(func() { execCommandIsolation = exec.Command })

	state := &PreIsolationState{
		OS:      "darwin",
		PFRules: "pass in all\npass out all\n",
	}
	if err := restoreOS(context.Background(), state); err != nil {
		t.Fatalf("restoreOS: %v", err)
	}
	foundRestore := false
	for _, c := range cmds {
		if strings.Contains(c, "pfctl") && strings.Contains(c, "-f") {
			foundRestore = true
		}
	}
	if !foundRestore {
		t.Fatalf("expected pfctl -f; got: %v", cmds)
	}
}
```

- [ ] **Step 2: Add PFRules to isolation.go**

Edit `agent/commands/isolation/isolation.go` — add `PFRules` field to struct and update `Execute`/`Rollback`:

```go
// PreIsolationState is captured before isolation and stored for rollback.
type PreIsolationState struct {
	OS            string `json:"os"`
	IPTablesRules string `json:"iptables_rules,omitempty"`
	NFTablesRules string `json:"nftables_rules,omitempty"`
	WFWRules      string `json:"wfw_rules,omitempty"`
	PFRules       string `json:"pf_rules,omitempty"`
}
```

Also update `Execute()` to serialize and `Rollback()` to deserialize `pf_rules`:

```go
func Execute(params map[string]any) (map[string]any, error) {
	// ... existing code ...
	return map[string]any{
		"os":             state.OS,
		"iptables_rules": state.IPTablesRules,
		"nftables_rules": state.NFTablesRules,
		"wfw_rules":      state.WFWRules,
		"pf_rules":       state.PFRules,
	}, nil
}

func Rollback(params map[string]any) (map[string]any, error) {
	state := &PreIsolationState{
		OS:            getStr(params, "os"),
		IPTablesRules: getStr(params, "iptables_rules"),
		NFTablesRules: getStr(params, "nftables_rules"),
		WFWRules:      getStr(params, "wfw_rules"),
		PFRules:       getStr(params, "pf_rules"),
	}
	// ... existing code ...
}
```

- [ ] **Step 3: Implement isolation_darwin.go**

Create `agent/commands/isolation/isolation_darwin.go`:

```go
//go:build darwin

package isolation

import (
	"context"
	"fmt"
	"net"
	"net/url"
	"os"
	"os/exec"
	"os/user"
	"strings"
)

// execCommandIsolation is mockable in tests.
var execCommandIsolation = exec.Command

func isolateOS(ctx context.Context, cfg IsolationConfig) (*PreIsolationState, error) {
	if u, _ := user.Current(); u != nil && u.Uid != "0" {
		return nil, fmt.Errorf("isolate_host requires root privileges")
	}

	state := &PreIsolationState{OS: "darwin"}

	// Capture current pf ruleset
	if out, err := execCommandIsolation("pfctl", "-s", "rules").CombinedOutput(); err == nil {
		state.PFRules = string(out)
	}

	cpIP, err := resolveHostDarwin(cfg.ControlPlaneURL)
	if err != nil {
		return nil, fmt.Errorf("cannot resolve control plane host: %w", err)
	}

	// Write restrictive ruleset to temp file
	rules := fmt.Sprintf(`# Nexplane isolation rules
pass in quick on lo0 all
pass out quick on lo0 all
pass out quick proto tcp to %s port 443
pass out quick proto tcp to %s port 22
block out all
block in all
`, cpIP, cfg.ManagementCIDR)

	tmpFile := "/tmp/nexplane-pf-iso.conf"
	if err := os.WriteFile(tmpFile, []byte(rules), 0600); err != nil {
		return nil, fmt.Errorf("writing pf rules: %w", err)
	}
	defer os.Remove(tmpFile)

	// Enable pf and load rules
	if out, err := execCommandIsolation("pfctl", "-e", "-f", tmpFile).CombinedOutput(); err != nil {
		return nil, fmt.Errorf("pfctl -e -f: %s: %w", out, err)
	}

	return state, nil
}

func restoreOS(ctx context.Context, state *PreIsolationState) error {
	if state.PFRules == "" {
		// pf was disabled before isolation — disable it again
		execCommandIsolation("pfctl", "-d").CombinedOutput() //nolint:errcheck
		return nil
	}

	tmpFile := "/tmp/nexplane-pf-restore.conf"
	if err := os.WriteFile(tmpFile, []byte(state.PFRules), 0600); err != nil {
		return fmt.Errorf("writing restore rules: %w", err)
	}
	defer os.Remove(tmpFile)

	if out, err := execCommandIsolation("pfctl", "-f", tmpFile).CombinedOutput(); err != nil {
		return fmt.Errorf("pfctl -f restore: %s: %w", out, err)
	}
	return nil
}

func resolveHostDarwin(rawURL string) (string, error) {
	u, err := url.Parse(rawURL)
	if err != nil {
		return "", err
	}
	host := u.Hostname()
	if net.ParseIP(host) != nil {
		return host, nil
	}
	addrs, err := net.LookupHost(host)
	if err != nil || len(addrs) == 0 {
		return "", fmt.Errorf("cannot resolve %q: %w", host, err)
	}
	return addrs[0], nil
}

// resolveHost satisfies the shared interface expected in isolation_test.go
func resolveHost(rawURL string) (string, error) {
	return resolveHostDarwin(rawURL)
}

func init() {
	_ = strings.Contains // suppress unused import if needed
}
```

- [ ] **Step 4: Fix isolation_other.go build tag**

Edit `agent/commands/isolation/isolation_other.go` line 1:
```
//go:build !linux && !darwin && !windows
```

- [ ] **Step 5: Run tests**

```bash
cd agent && go test ./commands/isolation/ -run TestIsolateDarwin -v
```
Expected: PASS

- [ ] **Step 6: Verify compilation for all platforms**

```bash
cd agent && GOOS=linux go build ./... && GOOS=darwin GOARCH=arm64 go build ./... && GOOS=windows go build ./... && echo "ALL OK"
```
Expected: ALL OK

- [ ] **Step 7: Commit**

```bash
git add agent/commands/isolation/isolation.go agent/commands/isolation/isolation_darwin.go agent/commands/isolation/isolation_darwin_test.go agent/commands/isolation/isolation_other.go
git commit -m "feat(agent): implement macOS host isolation via pfctl"
```

---

### Task 4: ossecurity/firewall_darwin.go

**Files:**
- Create: `agent/commands/ossecurity/firewall_darwin.go`
- Create: `agent/commands/ossecurity/firewall_darwin_test.go`

Note: `ossecurity_other.go` build tag fix happens in the final SP7 task after all darwin ossecurity files exist.

- [ ] **Step 1: Write failing test**

Create `agent/commands/ossecurity/firewall_darwin_test.go`:

```go
//go:build darwin

package ossecurity

import (
	"os/exec"
	"strings"
	"testing"
)

func TestFirewallExecuteOS_DarwinAddRule(t *testing.T) {
	var cmds []string
	execCommandFirewall = func(name string, args ...string) *exec.Cmd {
		cmds = append(cmds, name+" "+strings.Join(args, " "))
		return exec.Command("echo", "Application firewall is enabled. (State = 1)")
	}
	t.Cleanup(func() { execCommandFirewall = exec.Command })

	params := map[string]any{
		"action": "add_rule",
		"rule": map[string]any{
			"app_path": "/Applications/Foo.app",
		},
	}
	result, err := firewallExecuteOS(params)
	if err != nil {
		t.Fatalf("firewallExecuteOS: %v", err)
	}
	if result["snapshot"] == nil {
		t.Fatal("expected snapshot in result")
	}
	found := false
	for _, c := range cmds {
		if strings.Contains(c, "socketfilterfw") {
			found = true
		}
	}
	if !found {
		t.Fatalf("expected socketfilterfw call; got: %v", cmds)
	}
}

func TestFirewallRollbackOS_Darwin(t *testing.T) {
	var cmds []string
	execCommandFirewall = func(name string, args ...string) *exec.Cmd {
		cmds = append(cmds, name+" "+strings.Join(args, " "))
		return exec.Command("echo", "ok")
	}
	t.Cleanup(func() { execCommandFirewall = exec.Command })

	result, err := firewallRollbackOS(map[string]any{
		"snapshot":    "Application firewall is enabled.",
		"tool":        "socketfilterfw",
	})
	if err != nil {
		t.Fatalf("firewallRollbackOS: %v", err)
	}
	if result["rolled_back"] != true {
		t.Fatal("expected rolled_back=true")
	}
}
```

- [ ] **Step 2: Implement firewall_darwin.go**

Create `agent/commands/ossecurity/firewall_darwin.go`:

```go
//go:build darwin

package ossecurity

import (
	"fmt"
	"os/exec"
	"strings"
	"time"
)

const socketFilterFW = "/usr/libexec/ApplicationFirewall/socketfilterfw"

// execCommandFirewall is mockable in tests.
var execCommandFirewall = exec.Command

func firewallExecuteOS(params map[string]any) (map[string]any, error) {
	action, _ := params["action"].(string)

	// Snapshot current state
	snapOut, _ := execCommandFirewall(socketFilterFW, "--getglobalstate").Output()
	snapshot := strings.TrimSpace(string(snapOut))

	rule, _ := params["rule"].(map[string]any)

	switch action {
	case "add_rule":
		if appPath, _ := rule["app_path"].(string); appPath != "" {
			if out, err := execCommandFirewall(socketFilterFW, "--add", appPath).CombinedOutput(); err != nil {
				return nil, fmt.Errorf("socketfilterfw --add: %s: %w", out, err)
			}
			execCommandFirewall(socketFilterFW, "--unblockapp", appPath).Run() //nolint:errcheck
		} else if port, _ := rule["port"].(string); port != "" {
			// IP-level rule via pf anchor
			proto, _ := rule["protocol"].(string)
			if proto == "" {
				proto = "tcp"
			}
			anchor := fmt.Sprintf("pass in proto %s to any port %s\n", proto, port)
			if err := addPFAnchorRule(anchor); err != nil {
				return nil, err
			}
		}
	case "remove_rule":
		if appPath, _ := rule["app_path"].(string); appPath != "" {
			execCommandFirewall(socketFilterFW, "--remove", appPath).Run() //nolint:errcheck
		}
	case "flush":
		execCommandFirewall(socketFilterFW, "--setglobalstate", "off").Run() //nolint:errcheck
		execCommandFirewall(socketFilterFW, "--setglobalstate", "on").Run()  //nolint:errcheck
		removePFAnchor()
	case "enable":
		if out, err := execCommandFirewall(socketFilterFW, "--setglobalstate", "on").CombinedOutput(); err != nil {
			return nil, fmt.Errorf("socketfilterfw --setglobalstate on: %s: %w", out, err)
		}
		execCommandFirewall(socketFilterFW, "--setstealthmode", "on").Run() //nolint:errcheck
	}

	return map[string]any{
		"tool":       "socketfilterfw",
		"action":     action,
		"snapshot":   snapshot,
		"applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func firewallRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, _ := params["snapshot"].(string)

	// Restore global state based on snapshot string
	if strings.Contains(snapshot, "disabled") || strings.Contains(snapshot, "State = 0") {
		execCommandFirewall(socketFilterFW, "--setglobalstate", "off").Run() //nolint:errcheck
	} else {
		execCommandFirewall(socketFilterFW, "--setglobalstate", "on").Run() //nolint:errcheck
	}
	// Remove any pf anchor rules we added
	removePFAnchor()

	return map[string]any{"rolled_back": true}, nil
}

func addPFAnchorRule(rule string) error {
	content := "anchor \"nexplane\"\n" + rule
	cmd := execCommandFirewall("pfctl", "-a", "nexplane", "-f", "-")
	cmd.Stdin = strings.NewReader(content)
	if out, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("pfctl anchor: %s: %w", out, err)
	}
	return nil
}

func removePFAnchor() {
	execCommandFirewall("pfctl", "-a", "nexplane", "-F", "rules").Run() //nolint:errcheck
}
```

- [ ] **Step 3: Run tests**

```bash
cd agent && go test ./commands/ossecurity/ -run TestFirewall.*Darwin -v
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add agent/commands/ossecurity/firewall_darwin.go agent/commands/ossecurity/firewall_darwin_test.go
git commit -m "feat(agent): implement macOS application firewall via socketfilterfw"
```

---

### Task 5: Sync to EC2 and verify SP1+SP2 compile

- [ ] **Step 1: Sync code to EC2**

Run from Windows PowerShell (this must be done on EC2, not locally):

```bash
# On EC2:
cd /home/ec2-user/nexplane && git pull origin master
```

If git pull doesn't include changes (Windows edits not pushed yet), scp:
```bash
# From Windows (substitute actual paths):
scp -i ~/.ssh/id_ed25519 -r agent/commands/changip/changip_darwin*.go ec2-user@100.101.186.39:/home/ec2-user/nexplane/agent/commands/changip/
scp -i ~/.ssh/id_ed25519 -r agent/commands/reboot/reboot_darwin*.go ec2-user@100.101.186.39:/home/ec2-user/nexplane/agent/commands/reboot/
scp -i ~/.ssh/id_ed25519 agent/commands/isolation/isolation.go ec2-user@100.101.186.39:/home/ec2-user/nexplane/agent/commands/isolation/
scp -i ~/.ssh/id_ed25519 agent/commands/isolation/isolation_darwin*.go ec2-user@100.101.186.39:/home/ec2-user/nexplane/agent/commands/isolation/
scp -i ~/.ssh/id_ed25519 agent/commands/isolation/isolation_other.go ec2-user@100.101.186.39:/home/ec2-user/nexplane/agent/commands/isolation/
scp -i ~/.ssh/id_ed25519 agent/commands/ossecurity/firewall_darwin*.go ec2-user@100.101.186.39:/home/ec2-user/nexplane/agent/commands/ossecurity/
```

- [ ] **Step 2: Verify compilation on EC2**

```bash
# SSH to EC2: ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39
cd /home/ec2-user/nexplane/agent
GOOS=linux GOARCH=amd64 go build ./... && echo "linux OK"
GOOS=darwin GOARCH=arm64 go build ./... && echo "darwin OK"
GOOS=windows GOARCH=amd64 go build ./... && echo "windows OK"
```
Expected: three "OK" lines

- [ ] **Step 3: Run unit tests on EC2**

```bash
cd /home/ec2-user/nexplane/agent
go test ./commands/changip/ ./commands/reboot/ ./commands/isolation/ ./commands/ossecurity/ -v -count=1 2>&1 | tail -20
```
Expected: all PASS (darwin-tagged tests skipped on linux — that's OK)
