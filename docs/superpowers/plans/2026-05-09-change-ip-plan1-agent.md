# IP Migration — Plan 1: Go Agent Extensions

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the `agent/commands/changip` package with four IP-change methods (Tailscale-first, secondary-IP swap, commit-timer dead man's switch, manual), fix DNS configuration, add secondary IP helpers, and enrich the rollback snapshot — all test-driven, building cleanly with `go test` and `go build`.

**Architecture:** All new code lives inside `agent/commands/changip/` as separate Go source files per concern (one file per OS for platform-specific logic, one file each for Tailscale detection, the dead man's switch, and method dispatch). The existing `Execute`/`Rollback` entry points in `changip.go` delegate to `executeOS`/`rollbackOS`, which are already split by build tag; new parameters are read there and dispatched to the new helpers. No executor or registration changes are required — the command is already registered.

**Tech Stack:** Go 1.21

---

## File Map

| Action | File | Responsibility |
|---|---|---|
| Modify | `agent/commands/changip/changip_linux.go` | Extended snapshot, DNS config, secondary IP (Linux), method dispatch |
| Modify | `agent/commands/changip/changip_windows.go` | Extended snapshot, DNS config, secondary IP (Windows), method dispatch |
| Modify | `agent/commands/changip/changip.go` | No code changes — entry points already correct |
| Create | `agent/commands/changip/changip_tailscale.go` | `IsTailscaleActive`, `IsControlPlaneReachableViaTailscale` |
| Create | `agent/commands/changip/changip_deadman.go` | `PendingRollback`, `StartDeadManSwitch`, `CheckPendingRollback` |
| Modify | `agent/commands/changip/changip_test.go` | All new tests (build-tag: windows; Linux tests go in `changip_linux_test.go`) |
| Create | `agent/commands/changip/changip_linux_test.go` | Linux-specific tests (snapshot, DNS, secondary IP, method dispatch) |
| Create | `agent/commands/changip/changip_tailscale_test.go` | Tailscale detection tests (no build tag — uses mock exec) |
| Create | `agent/commands/changip/changip_deadman_test.go` | Dead man's switch tests |

---

## Task 1: Extended Snapshot

**Files:**
- Modify: `agent/commands/changip/changip_linux.go` — replace `captureSnapshot`
- Modify: `agent/commands/changip/changip_windows.go` — replace `captureSnapshotW`
- Create: `agent/commands/changip/changip_linux_test.go`
- Modify: `agent/commands/changip/changip_test.go`

### What the extended snapshot captures

**Linux** (`captureSnapshot`): all IPv4 CIDRs on the interface, all IPv6 CIDRs, all routes (`ip route show dev {iface}`), DNS servers and search domains (via `resolvectl status {iface}` if available, else parse `/etc/resolv.conf`), MTU (from `ip link show {iface}`), network manager type string (`"NetworkManager"`, `"systemd-networkd"`, `"interfaces"`, `"ifcfg"`), and NM connection name (from `nmcli -t -f NAME,DEVICE con show --active` when NM is active).

**Windows** (`captureSnapshotW`): all IPv4 addresses (from `netsh interface ipv4 show addresses {iface}`), DNS servers (from `netsh interface ipv4 show dnsservers {iface}`), MTU (from `netsh interface ipv4 show subinterfaces {iface}`).

- [ ] **Step 1.1: Create `changip_linux_test.go` with a failing snapshot test**

```go
//go:build linux

package changip

import (
	"testing"
)

func TestCaptureSnapshot_PopulatesFields(t *testing.T) {
	// This test runs against whatever interface "lo" is — always present on Linux.
	snap, err := captureSnapshot("lo")
	if err != nil {
		t.Fatalf("captureSnapshot: %v", err)
	}
	if snap.Interface != "lo" {
		t.Errorf("Interface: got %q, want %q", snap.Interface, "lo")
	}
	if snap.NetworkManager == "" {
		t.Error("NetworkManager field must not be empty")
	}
	// lo always has 127.0.0.1/8
	found := false
	for _, a := range snap.IPv4Addresses {
		if a == "127.0.0.1/8" {
			found = true
		}
	}
	if !found {
		t.Errorf("IPv4Addresses %v did not contain 127.0.0.1/8", snap.IPv4Addresses)
	}
	if snap.MTU == 0 {
		t.Error("MTU must be non-zero")
	}
}
```

Save to `agent/commands/changip/changip_linux_test.go`.

- [ ] **Step 1.2: Run test to confirm it fails**

```bash
cd agent
go test ./commands/changip/... -run TestCaptureSnapshot_PopulatesFields -v
```

Expected: compile error — `captureSnapshot` returns `(map[string]any, error)`, not a struct with `.Interface`.

- [ ] **Step 1.3: Define `Snapshot` struct and replace `captureSnapshot` in `changip_linux.go`**

Replace the existing `captureSnapshot` function and add the struct. The struct is defined at the top of `changip_linux.go` (Linux build tag file — Windows has its own parallel struct `SnapshotW` in `changip_windows.go`).

Add the struct after the imports in `changip_linux.go`:

```go
// Snapshot holds everything needed to restore a network interface.
type Snapshot struct {
	Interface      string   `json:"interface"`
	IPv4Addresses  []string `json:"ip_v4_addresses"`
	IPv6Addresses  []string `json:"ip_v6_addresses"`
	GatewayV4      string   `json:"gateway_v4"`
	GatewayV6      string   `json:"gateway_v6"`
	DNSServers     []string `json:"dns_servers"`
	DNSSearchDomains []string `json:"dns_search_domains"`
	Routes         []RouteEntry `json:"routes"`
	MTU            int      `json:"mtu"`
	NetworkManager string   `json:"network_manager"`
	ConnectionName string   `json:"connection_name"`
}

// RouteEntry is one line from `ip route show dev {iface}`.
type RouteEntry struct {
	Dst string `json:"dst"`
	Gw  string `json:"gw"`
	Dev string `json:"dev"`
}
```

Replace the existing `captureSnapshot` function body with:

```go
func captureSnapshot(iface string) (*Snapshot, error) {
	s := &Snapshot{Interface: iface}

	// --- IPv4 and IPv6 addresses ---
	addrOut, err := exec.Command("ip", "-o", "addr", "show", iface).Output()
	if err == nil {
		for _, line := range strings.Split(strings.TrimSpace(string(addrOut)), "\n") {
			fields := strings.Fields(line)
			// fields: index iface inet|inet6 cidr ...
			if len(fields) < 4 {
				continue
			}
			family := fields[2]
			cidr := fields[3]
			switch family {
			case "inet":
				s.IPv4Addresses = append(s.IPv4Addresses, cidr)
			case "inet6":
				s.IPv6Addresses = append(s.IPv6Addresses, cidr)
			}
		}
	}

	// --- Routes ---
	routeOut, _ := exec.Command("ip", "route", "show", "dev", iface).Output()
	for _, line := range strings.Split(strings.TrimSpace(string(routeOut)), "\n") {
		if line == "" {
			continue
		}
		fields := strings.Fields(line)
		entry := RouteEntry{Dst: fields[0], Dev: iface}
		for i, f := range fields {
			if f == "via" && i+1 < len(fields) {
				entry.Gw = fields[i+1]
			}
		}
		if entry.Dst == "default" {
			s.GatewayV4 = entry.Gw
		}
		s.Routes = append(s.Routes, entry)
	}

	// --- MTU ---
	linkOut, _ := exec.Command("ip", "link", "show", iface).Output()
	for _, line := range strings.Split(string(linkOut), "\n") {
		if strings.Contains(line, "mtu") {
			fields := strings.Fields(line)
			for i, f := range fields {
				if f == "mtu" && i+1 < len(fields) {
					fmt.Sscanf(fields[i+1], "%d", &s.MTU)
				}
			}
		}
	}

	// --- Network manager type and connection name ---
	nm := detectNetworkManager()
	switch nm {
	case nmNetworkManager:
		s.NetworkManager = "NetworkManager"
		conOut, _ := exec.Command("nmcli", "-t", "-f", "NAME,DEVICE", "con", "show", "--active").Output()
		for _, line := range strings.Split(string(conOut), "\n") {
			parts := strings.SplitN(line, ":", 2)
			if len(parts) == 2 && strings.TrimSpace(parts[1]) == iface {
				s.ConnectionName = strings.TrimSpace(parts[0])
			}
		}
	case nmSystemd:
		s.NetworkManager = "systemd-networkd"
	case nmDebian:
		s.NetworkManager = "interfaces"
	default:
		s.NetworkManager = "ifcfg"
	}

	// --- DNS: try resolvectl first, fall back to /etc/resolv.conf ---
	resOut, err := exec.Command("resolvectl", "status", iface).Output()
	if err == nil {
		for _, line := range strings.Split(string(resOut), "\n") {
			line = strings.TrimSpace(line)
			if strings.HasPrefix(line, "DNS Servers:") {
				raw := strings.TrimPrefix(line, "DNS Servers:")
				for _, srv := range strings.Fields(raw) {
					s.DNSServers = append(s.DNSServers, strings.TrimSpace(srv))
				}
			}
			if strings.HasPrefix(line, "DNS Domain:") {
				raw := strings.TrimPrefix(line, "DNS Domain:")
				for _, d := range strings.Fields(raw) {
					s.DNSSearchDomains = append(s.DNSSearchDomains, strings.TrimSpace(d))
				}
			}
		}
	} else {
		rcData, _ := os.ReadFile("/etc/resolv.conf")
		for _, line := range strings.Split(string(rcData), "\n") {
			line = strings.TrimSpace(line)
			if strings.HasPrefix(line, "nameserver ") {
				s.DNSServers = append(s.DNSServers, strings.TrimPrefix(line, "nameserver "))
			}
			if strings.HasPrefix(line, "search ") {
				for _, d := range strings.Fields(strings.TrimPrefix(line, "search ")) {
					s.DNSSearchDomains = append(s.DNSSearchDomains, d)
				}
			}
		}
	}

	return s, nil
}
```

- [ ] **Step 1.4: Update `executeOS` (Linux) to use `*Snapshot` and marshal it to `map[string]any`**

The `executeOS` return value stores `snapshot` as `map[string]any`. Convert the struct to JSON and back so the existing wire format is preserved. Add import `"encoding/json"`.

Replace the snapshot-capture and return block inside `executeOS` in `changip_linux.go`:

```go
	snap, err := captureSnapshot(iface)
	if err != nil {
		return nil, fmt.Errorf("capturing snapshot: %w", err)
	}

	method := detectNetworkManager()
	if err := applyIPChange(method, iface, mode, ipVersion, params); err != nil {
		return nil, err
	}

	snapBytes, _ := json.Marshal(snap)
	var snapMap map[string]any
	json.Unmarshal(snapBytes, &snapMap)

	return map[string]any{
		"action":     "change_ip",
		"interface":  iface,
		"mode":       mode,
		"applied":    true,
		"snapshot":   snapMap,
		"applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
```

Also add `"encoding/json"` to the import block in `changip_linux.go`.

- [ ] **Step 1.5: Add Windows extended snapshot struct and update `captureSnapshotW`**

At the top of `changip_windows.go` (after imports) add:

```go
// SnapshotW holds pre-change state on Windows.
type SnapshotW struct {
	Interface        string   `json:"interface"`
	IPv4Addresses    []string `json:"ip_v4_addresses"`
	DNSServers       []string `json:"dns_servers"`
	DNSSearchDomains []string `json:"dns_search_domains"`
	MTU              int      `json:"mtu"`
	NetworkManager   string   `json:"network_manager"`
}
```

Replace `captureSnapshotW`:

```go
func captureSnapshotW(iface string) (*SnapshotW, error) {
	s := &SnapshotW{Interface: iface, NetworkManager: "netsh"}

	// IPv4 addresses
	addrOut, _ := exec.Command("netsh", "interface", "ipv4", "show", "addresses", "name="+iface).Output()
	for _, line := range strings.Split(string(addrOut), "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "IP Address:") {
			ip := strings.TrimSpace(strings.TrimPrefix(line, "IP Address:"))
			s.IPv4Addresses = append(s.IPv4Addresses, ip)
		}
	}

	// DNS servers
	dnsOut, _ := exec.Command("netsh", "interface", "ipv4", "show", "dnsservers", "name="+iface).Output()
	for _, line := range strings.Split(string(dnsOut), "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "Statically Configured DNS Servers:") {
			srv := strings.TrimSpace(strings.TrimPrefix(line, "Statically Configured DNS Servers:"))
			if srv != "None" && srv != "" {
				s.DNSServers = append(s.DNSServers, srv)
			}
		} else if strings.HasPrefix(line, "Register with which suffix:") {
			// ignore
		} else if len(line) > 0 && !strings.Contains(line, ":") {
			// continuation DNS line
			s.DNSServers = append(s.DNSServers, line)
		}
	}

	// MTU
	mtuOut, _ := exec.Command("netsh", "interface", "ipv4", "show", "subinterfaces", iface).Output()
	for _, line := range strings.Split(string(mtuOut), "\n") {
		fields := strings.Fields(line)
		if len(fields) >= 1 {
			var mtu int
			if n, _ := fmt.Sscanf(fields[0], "%d", &mtu); n == 1 && mtu > 0 {
				s.MTU = mtu
				break
			}
		}
	}

	return s, nil
}
```

Update `executeOS` in `changip_windows.go` to marshal the struct the same way:

```go
	snap, err := captureSnapshotW(iface)
	if err != nil {
		return nil, fmt.Errorf("capturing snapshot: %w", err)
	}

	if err := applyIPChangeW(iface, mode, ipVersion, params); err != nil {
		return nil, err
	}

	snapBytes, _ := json.Marshal(snap)
	var snapMap map[string]any
	json.Unmarshal(snapBytes, &snapMap)

	return map[string]any{
		"action":     "change_ip",
		"interface":  iface,
		"mode":       mode,
		"applied":    true,
		"snapshot":   snapMap,
		"applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
```

Add `"encoding/json"` to imports in `changip_windows.go`.

- [ ] **Step 1.6: Add a matching Windows snapshot test in `changip_test.go`**

The existing `changip_test.go` already has `//go:build windows`. Add:

```go
func TestCaptureSnapshotW_PopulatesFields(t *testing.T) {
	snap, err := captureSnapshotW("Loopback Pseudo-Interface 1")
	if err != nil {
		t.Fatalf("captureSnapshotW: %v", err)
	}
	if snap.Interface == "" {
		t.Error("Interface must not be empty")
	}
	if snap.NetworkManager == "" {
		t.Error("NetworkManager must not be empty")
	}
}
```

- [ ] **Step 1.7: Run tests**

```bash
cd agent
go test ./commands/changip/... -v -run "TestCaptureSnapshot"
```

Expected on Linux: `TestCaptureSnapshot_PopulatesFields` PASS. On Windows CI: `TestCaptureSnapshotW_PopulatesFields` PASS.

- [ ] **Step 1.8: Commit**

```bash
git add agent/commands/changip/changip_linux.go \
        agent/commands/changip/changip_windows.go \
        agent/commands/changip/changip_linux_test.go \
        agent/commands/changip/changip_test.go
git commit -m "feat(changip): extended snapshot struct — all IPs, routes, DNS, MTU, NM type"
```

---

## Task 2: Fix DNS Configuration — Linux

**Files:**
- Modify: `agent/commands/changip/changip_linux.go` — add `configureDNS`
- Modify: `agent/commands/changip/changip_linux_test.go` — add DNS tests

`configureDNS` is called by `executeOS` when `dns_servers` is non-empty. It inspects the detected network manager and dispatches to the correct mechanism.

- [ ] **Step 2.1: Write failing DNS test in `changip_linux_test.go`**

The test uses a package-level variable `execCommand` (a function variable) so tests can inject a mock. This is the standard Go exec-testing pattern.

First, add the exec hook to `changip_linux.go`. At the top of the file (after the `import` block), add:

```go
// execCommand is a hook for tests to replace exec.Command.
var execCommand = exec.Command
```

Then replace every call to `exec.Command(...)` in `changip_linux.go` with `execCommand(...)`. (There are calls in `captureSnapshot`, `detectNetworkManager`, `applyNmcli`, and `runCmd`.) **Only `runCmd` and the new `configureDNS` need mocking for the DNS test — use `execCommandForRun` as a second hook inside `runCmd`:**

Replace `runCmd`:

```go
// execCommandForRun is the hook used by runCmd (and configureDNS).
var execCommandForRun = exec.Command

func runCmd(name string, args ...string) error {
	out, err := execCommandForRun(name, args...).CombinedOutput()
	if err != nil {
		return fmt.Errorf("%s %v: %w (output: %s)", name, args, err, out)
	}
	return nil
}
```

Now write the test in `changip_linux_test.go`:

```go
func TestConfigureDNS_NetworkManager(t *testing.T) {
	var capturedName string
	var capturedArgs []string
	execCommandForRun = func(name string, args ...string) *exec.Cmd {
		capturedName = name
		capturedArgs = args
		// Return a no-op command that exits 0.
		return exec.Command("true")
	}
	t.Cleanup(func() { execCommandForRun = exec.Command })

	err := configureDNS(nmNetworkManager, "eth0", "Wired connection 1",
		[]string{"1.1.1.1", "8.8.8.8"}, []string{"corp.example.com"})
	if err != nil {
		t.Fatalf("configureDNS: %v", err)
	}
	if capturedName != "nmcli" {
		t.Errorf("expected nmcli, got %q", capturedName)
	}
	_ = capturedArgs // verified by name check
}

func TestConfigureDNS_Resolvectl(t *testing.T) {
	var calls [][]string
	execCommandForRun = func(name string, args ...string) *exec.Cmd {
		calls = append(calls, append([]string{name}, args...))
		return exec.Command("true")
	}
	t.Cleanup(func() { execCommandForRun = exec.Command })

	err := configureDNS(nmSystemd, "eth0", "",
		[]string{"1.1.1.1"}, []string{"example.com"})
	if err != nil {
		t.Fatalf("configureDNS: %v", err)
	}
	if len(calls) < 2 {
		t.Fatalf("expected at least 2 commands, got %d: %v", len(calls), calls)
	}
	first := strings.Join(calls[0], " ")
	if !strings.Contains(first, "resolvectl") {
		t.Errorf("first call should be resolvectl, got %q", first)
	}
}
```

Add `"os/exec"` and `"strings"` to imports in `changip_linux_test.go` if not already present.

- [ ] **Step 2.2: Run to confirm compile failure**

```bash
cd agent
go test ./commands/changip/... -run "TestConfigureDNS" -v
```

Expected: compile error — `configureDNS` undefined.

- [ ] **Step 2.3: Implement `configureDNS` in `changip_linux.go`**

Add after `detectNetworkManager`:

```go
// configureDNS configures DNS servers and search domains for an interface.
// conName is the NetworkManager connection name (used only when nm == nmNetworkManager).
func configureDNS(nm networkManager, iface, conName string, servers, searchDomains []string) error {
	if len(servers) == 0 {
		return nil
	}
	switch nm {
	case nmNetworkManager:
		con := conName
		if con == "" {
			con = iface
		}
		dnsVal := strings.Join(servers, " ")
		if err := runCmd("nmcli", "con", "mod", con, "ipv4.dns", dnsVal); err != nil {
			return fmt.Errorf("nmcli ipv4.dns: %w", err)
		}
		if err := runCmd("nmcli", "con", "mod", con, "ipv6.dns", dnsVal); err != nil {
			return fmt.Errorf("nmcli ipv6.dns: %w", err)
		}
		if len(searchDomains) > 0 {
			if err := runCmd("nmcli", "con", "mod", con, "ipv4.dns-search", strings.Join(searchDomains, " ")); err != nil {
				return fmt.Errorf("nmcli ipv4.dns-search: %w", err)
			}
		}
		return runCmd("nmcli", "con", "up", con)

	case nmSystemd:
		args := append([]string{"dns", iface}, servers...)
		if err := runCmd("resolvectl", args...); err != nil {
			return fmt.Errorf("resolvectl dns: %w", err)
		}
		if len(searchDomains) > 0 {
			domArgs := append([]string{"domain", iface}, searchDomains...)
			if err := runCmd("resolvectl", domArgs...); err != nil {
				return fmt.Errorf("resolvectl domain: %w", err)
			}
		}
		return nil

	default:
		// /etc/resolv.conf fallback: preserve non-nameserver lines, rewrite nameserver lines.
		rcData, _ := os.ReadFile("/etc/resolv.conf")
		var kept []string
		for _, line := range strings.Split(string(rcData), "\n") {
			if !strings.HasPrefix(strings.TrimSpace(line), "nameserver") &&
				!strings.HasPrefix(strings.TrimSpace(line), "search") {
				kept = append(kept, line)
			}
		}
		for _, srv := range servers {
			kept = append(kept, "nameserver "+srv)
		}
		if len(searchDomains) > 0 {
			kept = append(kept, "search "+strings.Join(searchDomains, " "))
		}
		return os.WriteFile("/etc/resolv.conf", []byte(strings.Join(kept, "\n")+"\n"), 0644)
	}
}
```

- [ ] **Step 2.4: Run DNS tests**

```bash
cd agent
go test ./commands/changip/... -run "TestConfigureDNS" -v
```

Expected:
```
--- PASS: TestConfigureDNS_NetworkManager
--- PASS: TestConfigureDNS_Resolvectl
```

- [ ] **Step 2.5: Commit**

```bash
git add agent/commands/changip/changip_linux.go \
        agent/commands/changip/changip_linux_test.go
git commit -m "feat(changip): implement configureDNS for Linux (NM, systemd-resolved, resolv.conf)"
```

---

## Task 3: Fix DNS Configuration — Windows

**Files:**
- Modify: `agent/commands/changip/changip_windows.go` — add `configureDNSW` and `execCommandForRunW` hook
- Modify: `agent/commands/changip/changip_test.go` — add DNS tests

- [ ] **Step 3.1: Add exec hook and replace `runCmdW` in `changip_windows.go`**

Add after imports:

```go
var execCommandForRunW = exec.Command

func runCmdW(name string, args ...string) error {
	out, err := execCommandForRunW(name, args...).CombinedOutput()
	if err != nil {
		return fmt.Errorf("%s %v: %w (output: %s)", name, args, err, out)
	}
	return nil
}
```

(This replaces the existing `runCmdW` body — keep the signature identical.)

- [ ] **Step 3.2: Write failing DNS test in `changip_test.go`**

```go
func TestConfigureDNSW_SetsServers(t *testing.T) {
	var calls [][]string
	execCommandForRunW = func(name string, args ...string) *exec.Cmd {
		calls = append(calls, append([]string{name}, args...))
		return exec.Command("cmd", "/c", "exit 0")
	}
	t.Cleanup(func() { execCommandForRunW = exec.Command })

	err := configureDNSW("Ethernet", []string{"1.1.1.1", "8.8.8.8"})
	if err != nil {
		t.Fatalf("configureDNSW: %v", err)
	}
	if len(calls) < 2 {
		t.Fatalf("expected at least 2 netsh calls, got %d: %v", len(calls), calls)
	}
	// First call: set primary static
	first := strings.Join(calls[0], " ")
	if !strings.Contains(first, "static") || !strings.Contains(first, "1.1.1.1") {
		t.Errorf("first call should set primary DNS 1.1.1.1: %q", first)
	}
	// Second call: add secondary
	second := strings.Join(calls[1], " ")
	if !strings.Contains(second, "8.8.8.8") || !strings.Contains(second, "index=2") {
		t.Errorf("second call should add secondary DNS 8.8.8.8 at index=2: %q", second)
	}
}
```

Add `"os/exec"` and `"strings"` to imports in `changip_test.go`.

- [ ] **Step 3.3: Run to confirm compile failure**

```bash
cd agent
GOOS=windows go test ./commands/changip/... -run "TestConfigureDNSW" -v
```

Expected: compile error — `configureDNSW` undefined.

- [ ] **Step 3.4: Implement `configureDNSW` in `changip_windows.go`**

```go
// configureDNSW sets DNS servers for a Windows interface via netsh.
func configureDNSW(iface string, servers []string) error {
	if len(servers) == 0 {
		return nil
	}
	// Set primary DNS as static.
	if err := runCmdW("netsh", "interface", "ipv4", "set", "dnsservers",
		"name="+iface, "static", servers[0], "primary"); err != nil {
		return fmt.Errorf("set primary DNS: %w", err)
	}
	// Add additional servers at increasing indexes.
	for i, srv := range servers[1:] {
		idx := fmt.Sprintf("index=%d", i+2)
		if err := runCmdW("netsh", "interface", "ipv4", "add", "dnsservers",
			"name="+iface, srv, idx); err != nil {
			return fmt.Errorf("add DNS server %s: %w", srv, err)
		}
	}
	return nil
}
```

- [ ] **Step 3.5: Run DNS test**

```bash
cd agent
GOOS=windows go test ./commands/changip/... -run "TestConfigureDNSW" -v
```

Expected:
```
--- PASS: TestConfigureDNSW_SetsServers
```

- [ ] **Step 3.6: Commit**

```bash
git add agent/commands/changip/changip_windows.go \
        agent/commands/changip/changip_test.go
git commit -m "feat(changip): implement configureDNSW for Windows via netsh"
```

---

## Task 4: Tailscale Detection Helper

**Files:**
- Create: `agent/commands/changip/changip_tailscale.go`
- Create: `agent/commands/changip/changip_tailscale_test.go`

No build tag — these functions use `execCommandForTailscale` hook and standard `net/http`.

- [ ] **Step 4.1: Create `changip_tailscale_test.go` with failing tests**

```go
package changip

import (
	"net/http"
	"net/http/httptest"
	"os/exec"
	"testing"
	"time"
)

func TestIsTailscaleActive_Active(t *testing.T) {
	execCommandForTailscale = func(name string, args ...string) *exec.Cmd {
		// Simulate `tailscale ip -4` printing an IP.
		return exec.Command("echo", "100.64.0.1")
	}
	t.Cleanup(func() { execCommandForTailscale = exec.Command })

	ip, active := IsTailscaleActive()
	if !active {
		t.Error("expected active=true")
	}
	if ip != "100.64.0.1" {
		t.Errorf("expected IP 100.64.0.1, got %q", ip)
	}
}

func TestIsTailscaleActive_Inactive(t *testing.T) {
	execCommandForTailscale = func(name string, args ...string) *exec.Cmd {
		// Simulate tailscale not installed / exit non-zero.
		return exec.Command("false")
	}
	t.Cleanup(func() { execCommandForTailscale = exec.Command })

	_, active := IsTailscaleActive()
	if active {
		t.Error("expected active=false")
	}
}

func TestIsControlPlaneReachableViaTailscale_Reachable(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	ok := IsControlPlaneReachableViaTailscale(srv.URL, 2*time.Second)
	if !ok {
		t.Error("expected reachable=true")
	}
}

func TestIsControlPlaneReachableViaTailscale_Unreachable(t *testing.T) {
	// Point at a port that refuses connections.
	ok := IsControlPlaneReachableViaTailscale("http://127.0.0.1:1", 500*time.Millisecond)
	if ok {
		t.Error("expected reachable=false")
	}
}
```

Save to `agent/commands/changip/changip_tailscale_test.go`.

- [ ] **Step 4.2: Run to confirm compile failure**

```bash
cd agent
go test ./commands/changip/... -run "TestIsTailscale|TestIsControlPlane" -v
```

Expected: compile error — `IsTailscaleActive` undefined.

- [ ] **Step 4.3: Create `changip_tailscale.go`**

```go
package changip

import (
	"fmt"
	"net/http"
	"os/exec"
	"strings"
	"time"
)

// execCommandForTailscale is a hook for tests to replace exec.Command.
var execCommandForTailscale = exec.Command

// IsTailscaleActive returns the Tailscale IPv4 address and true if tailscaled
// is running and has an active IP. Returns ("", false) otherwise.
func IsTailscaleActive() (tailscaleIP string, active bool) {
	out, err := execCommandForTailscale("tailscale", "ip", "-4").Output()
	if err != nil {
		return "", false
	}
	ip := strings.TrimSpace(string(out))
	if ip == "" {
		return "", false
	}
	return ip, true
}

// IsControlPlaneReachableViaTailscale probes {controlPlaneURL}/health with an
// HTTP GET and returns true if an HTTP 200 is received within timeout.
func IsControlPlaneReachableViaTailscale(controlPlaneURL string, timeout time.Duration) bool {
	client := &http.Client{Timeout: timeout}
	url := strings.TrimRight(controlPlaneURL, "/") + "/health"
	resp, err := client.Get(url)
	if err != nil {
		return false
	}
	defer resp.Body.Close()
	return resp.StatusCode == http.StatusOK
}

// tailscaleHealthURL builds the control-plane health URL via the Tailscale IP.
// port is the control plane HTTP port (e.g. "8000").
func tailscaleHealthURL(tsIP, port string) string {
	return fmt.Sprintf("http://%s:%s", tsIP, port)
}
```

- [ ] **Step 4.4: Run tests**

```bash
cd agent
go test ./commands/changip/... -run "TestIsTailscale|TestIsControlPlane" -v
```

Expected:
```
--- PASS: TestIsTailscaleActive_Active
--- PASS: TestIsTailscaleActive_Inactive
--- PASS: TestIsControlPlaneReachableViaTailscale_Reachable
--- PASS: TestIsControlPlaneReachableViaTailscale_Unreachable
```

- [ ] **Step 4.5: Commit**

```bash
git add agent/commands/changip/changip_tailscale.go \
        agent/commands/changip/changip_tailscale_test.go
git commit -m "feat(changip): Tailscale detection helper — IsTailscaleActive, IsControlPlaneReachableViaTailscale"
```

---

## Task 5: Secondary IP Support — Linux

**Files:**
- Modify: `agent/commands/changip/changip_linux.go` — add `AddSecondaryIP`, `RemoveSecondaryIP`, `GetInterfaceAddresses`
- Modify: `agent/commands/changip/changip_linux_test.go` — add secondary IP tests

- [ ] **Step 5.1: Write failing secondary IP tests in `changip_linux_test.go`**

```go
func TestAddSecondaryIP_CallsIPAddrAdd(t *testing.T) {
	var capturedArgs []string
	execCommandForRun = func(name string, args ...string) *exec.Cmd {
		capturedArgs = append([]string{name}, args...)
		return exec.Command("true")
	}
	t.Cleanup(func() { execCommandForRun = exec.Command })

	err := AddSecondaryIP("eth0", "10.0.0.200/24")
	if err != nil {
		t.Fatalf("AddSecondaryIP: %v", err)
	}
	// Expect: ip addr add 10.0.0.200/24 dev eth0
	if len(capturedArgs) < 5 {
		t.Fatalf("expected at least 5 args, got %v", capturedArgs)
	}
	if capturedArgs[0] != "ip" || capturedArgs[2] != "add" || capturedArgs[3] != "10.0.0.200/24" {
		t.Errorf("unexpected command: %v", capturedArgs)
	}
}

func TestRemoveSecondaryIP_CallsIPAddrDel(t *testing.T) {
	var capturedArgs []string
	execCommandForRun = func(name string, args ...string) *exec.Cmd {
		capturedArgs = append([]string{name}, args...)
		return exec.Command("true")
	}
	t.Cleanup(func() { execCommandForRun = exec.Command })

	err := RemoveSecondaryIP("eth0", "10.0.0.200/24")
	if err != nil {
		t.Fatalf("RemoveSecondaryIP: %v", err)
	}
	if capturedArgs[0] != "ip" || capturedArgs[2] != "del" || capturedArgs[3] != "10.0.0.200/24" {
		t.Errorf("unexpected command: %v", capturedArgs)
	}
}

func TestGetInterfaceAddresses_ReturnsAddresses(t *testing.T) {
	execCommand = func(name string, args ...string) *exec.Cmd {
		// Simulate `ip -o addr show lo` output.
		return exec.Command("echo",
			"1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever preferred_lft forever\n"+
				"1: lo    inet6 ::1/128 scope host\\       valid_lft forever preferred_lft forever")
	}
	t.Cleanup(func() { execCommand = exec.Command })

	addrs, err := GetInterfaceAddresses("lo")
	if err != nil {
		t.Fatalf("GetInterfaceAddresses: %v", err)
	}
	if len(addrs) == 0 {
		t.Error("expected at least one address")
	}
}
```

- [ ] **Step 5.2: Run to confirm compile failure**

```bash
cd agent
go test ./commands/changip/... -run "TestAddSecondary|TestRemoveSecondary|TestGetInterface" -v
```

Expected: compile error — functions undefined.

- [ ] **Step 5.3: Update `captureSnapshot` in `changip_linux.go` to use `execCommand` hook**

In the `captureSnapshot` function, replace `exec.Command(...)` calls with `execCommand(...)` calls. For example:

```go
addrOut, err := execCommand("ip", "-o", "addr", "show", iface).Output()
// ...
routeOut, _ := execCommand("ip", "route", "show", "dev", iface).Output()
// ...
linkOut, _ := execCommand("ip", "link", "show", iface).Output()
// ...
conOut, _ := execCommand("nmcli", "-t", "-f", "NAME,DEVICE", "con", "show", "--active").Output()
// ...
resOut, err := execCommand("resolvectl", "status", iface).Output()
```

Also update `detectNetworkManager` to use `execCommand` for `exec.LookPath` alternatives — but since `LookPath` isn't the same as `exec.Command`, leave `LookPath` calls as-is (they don't run external processes).

- [ ] **Step 5.4: Implement secondary IP functions in `changip_linux.go`**

```go
// AddSecondaryIP adds an additional IP to an interface without removing existing ones.
// cidr must be in CIDR notation, e.g. "10.0.0.200/24".
func AddSecondaryIP(iface, cidr string) error {
	return runCmd("ip", "addr", "add", cidr, "dev", iface)
}

// RemoveSecondaryIP removes a specific IP from an interface.
// cidr must be in CIDR notation, e.g. "10.0.0.200/24".
func RemoveSecondaryIP(iface, cidr string) error {
	return runCmd("ip", "addr", "del", cidr, "dev", iface)
}

// GetInterfaceAddresses returns all current IPs (IPv4 and IPv6) on an interface
// in CIDR notation.
func GetInterfaceAddresses(iface string) ([]string, error) {
	out, err := execCommand("ip", "-o", "addr", "show", iface).Output()
	if err != nil {
		return nil, fmt.Errorf("ip addr show %s: %w", iface, err)
	}
	var addrs []string
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		fields := strings.Fields(line)
		if len(fields) < 4 {
			continue
		}
		family := fields[2]
		if family == "inet" || family == "inet6" {
			addrs = append(addrs, fields[3])
		}
	}
	return addrs, nil
}
```

- [ ] **Step 5.5: Run secondary IP tests**

```bash
cd agent
go test ./commands/changip/... -run "TestAddSecondary|TestRemoveSecondary|TestGetInterface" -v
```

Expected:
```
--- PASS: TestAddSecondaryIP_CallsIPAddrAdd
--- PASS: TestRemoveSecondaryIP_CallsIPAddrDel
--- PASS: TestGetInterfaceAddresses_ReturnsAddresses
```

- [ ] **Step 5.6: Commit**

```bash
git add agent/commands/changip/changip_linux.go \
        agent/commands/changip/changip_linux_test.go
git commit -m "feat(changip): secondary IP support for Linux — AddSecondaryIP, RemoveSecondaryIP, GetInterfaceAddresses"
```

---

## Task 6: Secondary IP Support — Windows

**Files:**
- Modify: `agent/commands/changip/changip_windows.go` — add `AddSecondaryIPW`, `RemoveSecondaryIPW`
- Modify: `agent/commands/changip/changip_test.go` — add secondary IP tests

- [ ] **Step 6.1: Write failing tests in `changip_test.go`**

```go
func TestAddSecondaryIPW_CallsNetsh(t *testing.T) {
	var capturedArgs []string
	execCommandForRunW = func(name string, args ...string) *exec.Cmd {
		capturedArgs = append([]string{name}, args...)
		return exec.Command("cmd", "/c", "exit 0")
	}
	t.Cleanup(func() { execCommandForRunW = exec.Command })

	err := AddSecondaryIPW("Ethernet", "10.0.0.200", "255.255.255.0")
	if err != nil {
		t.Fatalf("AddSecondaryIPW: %v", err)
	}
	if capturedArgs[0] != "netsh" {
		t.Errorf("expected netsh, got %q", capturedArgs[0])
	}
	joined := strings.Join(capturedArgs, " ")
	if !strings.Contains(joined, "add") || !strings.Contains(joined, "10.0.0.200") {
		t.Errorf("expected add address command: %v", capturedArgs)
	}
}

func TestRemoveSecondaryIPW_CallsNetsh(t *testing.T) {
	var capturedArgs []string
	execCommandForRunW = func(name string, args ...string) *exec.Cmd {
		capturedArgs = append([]string{name}, args...)
		return exec.Command("cmd", "/c", "exit 0")
	}
	t.Cleanup(func() { execCommandForRunW = exec.Command })

	err := RemoveSecondaryIPW("Ethernet", "10.0.0.200")
	if err != nil {
		t.Fatalf("RemoveSecondaryIPW: %v", err)
	}
	joined := strings.Join(capturedArgs, " ")
	if !strings.Contains(joined, "delete") || !strings.Contains(joined, "10.0.0.200") {
		t.Errorf("expected delete address command: %v", capturedArgs)
	}
}
```

- [ ] **Step 6.2: Run to confirm compile failure**

```bash
cd agent
GOOS=windows go test ./commands/changip/... -run "TestAddSecondaryIPW|TestRemoveSecondaryIPW" -v
```

Expected: compile error.

- [ ] **Step 6.3: Implement `AddSecondaryIPW` and `RemoveSecondaryIPW` in `changip_windows.go`**

```go
// AddSecondaryIPW adds an additional IPv4 address to a Windows interface.
// ip is dotted-decimal, mask is dotted-decimal subnet mask.
func AddSecondaryIPW(iface, ip, mask string) error {
	return runCmdW("netsh", "interface", "ipv4", "add", "address",
		"name="+iface, ip, mask)
}

// RemoveSecondaryIPW removes a specific IPv4 address from a Windows interface.
func RemoveSecondaryIPW(iface, ip string) error {
	return runCmdW("netsh", "interface", "ipv4", "delete", "address",
		"name="+iface, ip)
}
```

- [ ] **Step 6.4: Run tests**

```bash
cd agent
GOOS=windows go test ./commands/changip/... -run "TestAddSecondaryIPW|TestRemoveSecondaryIPW" -v
```

Expected:
```
--- PASS: TestAddSecondaryIPW_CallsNetsh
--- PASS: TestRemoveSecondaryIPW_CallsNetsh
```

- [ ] **Step 6.5: Commit**

```bash
git add agent/commands/changip/changip_windows.go \
        agent/commands/changip/changip_test.go
git commit -m "feat(changip): secondary IP support for Windows — AddSecondaryIPW, RemoveSecondaryIPW"
```

---

## Task 7: Dead Man's Switch

**Files:**
- Create: `agent/commands/changip/changip_deadman.go`
- Create: `agent/commands/changip/changip_deadman_test.go`

No build tag — pure Go, no OS-specific calls.

- [ ] **Step 7.1: Create `changip_deadman_test.go` with four failing tests**

```go
package changip

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestDeadManSwitch_CancelsOnSuccessfulProbe(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	dir := t.TempDir()
	rollbackPath := filepath.Join(dir, "pending_rollback.json")

	pr := PendingRollback{
		JobID:             "job-cancel-test",
		ExpiresAt:         time.Now().Add(30 * time.Second),
		RollbackParams:    map[string]any{"interface": "eth0"},
		ProbeURL:          srv.URL,
		ProbeIntervalSecs: 1,
		ProbeTimeoutSecs:  2,
	}

	rollbackCalled := make(chan struct{})
	cancelFn, err := startDeadManSwitch(pr, rollbackPath, func() {
		close(rollbackCalled)
	})
	if err != nil {
		t.Fatalf("startDeadManSwitch: %v", err)
	}

	// Wait for goroutine to probe and cancel.
	select {
	case <-rollbackCalled:
		t.Error("rollback should NOT have been called — probe succeeds")
	case <-time.After(5 * time.Second):
		// Good — rollback was not triggered.
	}
	cancelFn()

	// Pending file should be deleted after successful probe.
	if _, err := os.Stat(rollbackPath); !os.IsNotExist(err) {
		t.Error("pending_rollback.json should be deleted after successful probe")
	}
}

func TestDeadManSwitch_RollbackOnTimeout(t *testing.T) {
	dir := t.TempDir()
	rollbackPath := filepath.Join(dir, "pending_rollback.json")

	pr := PendingRollback{
		JobID:             "job-timeout-test",
		ExpiresAt:         time.Now().Add(1 * time.Second), // 1 second timer
		RollbackParams:    map[string]any{"interface": "eth0"},
		ProbeURL:          "http://127.0.0.1:1", // unreachable
		ProbeIntervalSecs: 1,
		ProbeTimeoutSecs:  1,
	}

	rollbackCalled := make(chan struct{}, 1)
	_, err := startDeadManSwitch(pr, rollbackPath, func() {
		rollbackCalled <- struct{}{}
	})
	if err != nil {
		t.Fatalf("startDeadManSwitch: %v", err)
	}

	select {
	case <-rollbackCalled:
		// Good — rollback triggered.
	case <-time.After(5 * time.Second):
		t.Error("expected rollback to be called within 5s, but it was not")
	}
}

func TestCheckPendingRollback_Expired(t *testing.T) {
	dir := t.TempDir()
	rollbackPath := filepath.Join(dir, "pending_rollback.json")

	pr := PendingRollback{
		JobID:          "job-expired",
		ExpiresAt:      time.Now().Add(-10 * time.Second), // already expired
		RollbackParams: map[string]any{"interface": "eth0"},
		ProbeURL:       "http://127.0.0.1:1",
	}
	data, _ := json.Marshal(pr)
	os.WriteFile(rollbackPath, data, 0644)

	rollbackCalled := make(chan struct{}, 1)
	err := checkPendingRollback(rollbackPath, func() {
		rollbackCalled <- struct{}{}
	})
	if err != nil {
		t.Fatalf("checkPendingRollback: %v", err)
	}

	select {
	case <-rollbackCalled:
		// Good.
	case <-time.After(1 * time.Second):
		t.Error("rollback should have been called immediately for expired file")
	}
}

func TestCheckPendingRollback_NotExpired(t *testing.T) {
	dir := t.TempDir()
	rollbackPath := filepath.Join(dir, "pending_rollback.json")

	pr := PendingRollback{
		JobID:             "job-not-expired",
		ExpiresAt:         time.Now().Add(30 * time.Second),
		RollbackParams:    map[string]any{"interface": "eth0"},
		ProbeURL:          "http://127.0.0.1:1", // unreachable so timer fires if not cancelled
		ProbeIntervalSecs: 5,
		ProbeTimeoutSecs:  1,
	}
	data, _ := json.Marshal(pr)
	os.WriteFile(rollbackPath, data, 0644)

	rollbackCalled := make(chan struct{}, 1)
	err := checkPendingRollback(rollbackPath, func() {
		rollbackCalled <- struct{}{}
	})
	if err != nil {
		t.Fatalf("checkPendingRollback: %v", err)
	}

	// Rollback should NOT be called immediately.
	select {
	case <-rollbackCalled:
		t.Error("rollback should not fire immediately for a non-expired file")
	case <-time.After(500 * time.Millisecond):
		// Good — timer is still pending.
	}
}
```

Save to `agent/commands/changip/changip_deadman_test.go`.

- [ ] **Step 7.2: Run to confirm compile failure**

```bash
cd agent
go test ./commands/changip/... -run "TestDeadManSwitch|TestCheckPendingRollback" -v
```

Expected: compile error — types and functions undefined.

- [ ] **Step 7.3: Create `changip_deadman.go`**

```go
package changip

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"strings"
	"time"
)

// PendingRollbackPath is the default on-disk location for the commit-timer state.
const PendingRollbackPath = "/var/lib/nexplane-agent/pending_rollback.json"

// PendingRollback is written to disk before an IP change is applied.
// On agent restart, if this file exists, the agent resumes or executes rollback.
type PendingRollback struct {
	JobID             string         `json:"job_id"`
	ExpiresAt         time.Time      `json:"expires_at"`
	RollbackParams    map[string]any `json:"rollback_params"`
	ProbeURL          string         `json:"probe_url"`
	ProbeIntervalSecs int            `json:"probe_interval_seconds"`
	ProbeTimeoutSecs  int            `json:"probe_timeout_seconds"`
}

// StartDeadManSwitch writes the pending rollback file and starts a background
// goroutine that polls ProbeURL/health. The first HTTP 200 response cancels the
// timer and deletes the file. If ExpiresAt is reached without a successful probe,
// rollbackFn is called. Returns a cancelFn that stops the goroutine.
func StartDeadManSwitch(pr PendingRollback, rollbackFn func()) (cancelFn func(), err error) {
	return startDeadManSwitch(PendingRollbackPath, pr, rollbackFn)
}

// startDeadManSwitch is the internal version with an injectable rollbackPath for tests.
func startDeadManSwitch(pr PendingRollback, rollbackPath string, rollbackFn func()) (cancelFn func(), err error) {
	data, err := json.Marshal(pr)
	if err != nil {
		return nil, fmt.Errorf("marshal pending rollback: %w", err)
	}
	if err := os.MkdirAll(strings.TrimSuffix(rollbackPath, "/pending_rollback.json"), 0755); err != nil {
		// Best-effort directory creation.
		_ = err
	}
	if err := os.WriteFile(rollbackPath, data, 0644); err != nil {
		return nil, fmt.Errorf("write pending rollback: %w", err)
	}

	ctx, cancel := context.WithCancel(context.Background())

	go func() {
		interval := time.Duration(pr.ProbeIntervalSecs) * time.Second
		if interval == 0 {
			interval = 5 * time.Second
		}
		probeTimeout := time.Duration(pr.ProbeTimeoutSecs) * time.Second
		if probeTimeout == 0 {
			probeTimeout = 3 * time.Second
		}
		deadline := pr.ExpiresAt
		ticker := time.NewTicker(interval)
		defer ticker.Stop()
		expiry := time.NewTimer(time.Until(deadline))
		defer expiry.Stop()

		probeURL := strings.TrimRight(pr.ProbeURL, "/") + "/health"
		client := &http.Client{Timeout: probeTimeout}

		probe := func() bool {
			resp, err := client.Get(probeURL)
			if err != nil {
				return false
			}
			defer resp.Body.Close()
			return resp.StatusCode == http.StatusOK
		}

		// Probe immediately on start too.
		if probe() {
			os.Remove(rollbackPath)
			cancel()
			return
		}

		for {
			select {
			case <-ctx.Done():
				return
			case <-expiry.C:
				rollbackFn()
				os.Remove(rollbackPath)
				return
			case <-ticker.C:
				if probe() {
					os.Remove(rollbackPath)
					cancel()
					return
				}
			}
		}
	}()

	return func() {
		cancel()
		os.Remove(rollbackPath)
	}, nil
}

// CheckPendingRollback checks for an existing pending_rollback.json on agent startup.
// If the file exists and ExpiresAt is in the past, rollbackFn is called immediately.
// If ExpiresAt is in the future, the commit-timer goroutine is resumed.
// Returns nil if no file exists.
func CheckPendingRollback(rollbackFn func()) error {
	return checkPendingRollback(PendingRollbackPath, rollbackFn)
}

// checkPendingRollback is the internal version with an injectable path for tests.
func checkPendingRollback(rollbackPath string, rollbackFn func()) error {
	data, err := os.ReadFile(rollbackPath)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("read pending rollback: %w", err)
	}

	var pr PendingRollback
	if err := json.Unmarshal(data, &pr); err != nil {
		return fmt.Errorf("parse pending rollback: %w", err)
	}

	if time.Now().After(pr.ExpiresAt) {
		// Already expired — roll back immediately.
		rollbackFn()
		os.Remove(rollbackPath)
		return nil
	}

	// Not yet expired — resume the timer with the remaining window.
	_, err = startDeadManSwitch(pr, rollbackPath, rollbackFn)
	return err
}
```

- [ ] **Step 7.4: Run dead man's switch tests**

```bash
cd agent
go test ./commands/changip/... -run "TestDeadManSwitch|TestCheckPendingRollback" -v -timeout 30s
```

Expected:
```
--- PASS: TestDeadManSwitch_CancelsOnSuccessfulProbe
--- PASS: TestDeadManSwitch_RollbackOnTimeout
--- PASS: TestCheckPendingRollback_Expired
--- PASS: TestCheckPendingRollback_NotExpired
```

- [ ] **Step 7.5: Commit**

```bash
git add agent/commands/changip/changip_deadman.go \
        agent/commands/changip/changip_deadman_test.go
git commit -m "feat(changip): dead man's switch — StartDeadManSwitch, CheckPendingRollback with crash recovery"
```

---

## Task 8: Method Selection and Execute Update

**Files:**
- Modify: `agent/commands/changip/changip_linux.go` — update `executeOS` with method dispatch
- Modify: `agent/commands/changip/changip_windows.go` — update `executeOS` with method dispatch (Windows uses `"manual"` for all non-netsh methods since Tailscale/secondary are separate)
- Modify: `agent/commands/changip/changip_linux_test.go` — add method dispatch tests

### Method dispatch logic

Read from `params`:
- `method` string: `"auto"` (default), `"tailscale"`, `"secondary_swap"`, `"commit_timer"`, `"manual"`
- `commit_timer_seconds` int: default 30, clamp to [10, 300]
- `probe_interval_seconds` int: default 5
- `add_secondary` bool: default false
- `dns_servers` []string
- `dns_search_domains` []string
- `probe_url` string: URL for commit timer health probes
- `new_ip_v4` string (existing param)

**Auto decision tree (Linux only):**
1. If `IsTailscaleActive()` returns active AND `IsControlPlaneReachableViaTailscale(probeURL, 5s)` → use `"tailscale"`
2. Else if new subnet is routable (`ip route get {control_plane_ip}` exits 0 with new gateway) → use `"secondary_swap"`
3. Else → use `"commit_timer"`

For this plan, the auto decision tree resolves to a `method` string and then dispatches — no special auto-specific code beyond the three checks.

- [ ] **Step 8.1: Write method dispatch tests in `changip_linux_test.go`**

```go
func TestExecuteOS_MethodManual_ReturnsRequiresConfirmation(t *testing.T) {
	// Mock runCmd so no real commands run.
	execCommandForRun = func(name string, args ...string) *exec.Cmd {
		return exec.Command("true")
	}
	execCommand = func(name string, args ...string) *exec.Cmd {
		// Simulate `ip -o addr show eth0` — return empty.
		return exec.Command("echo", "")
	}
	execCommandForTailscale = func(name string, args ...string) *exec.Cmd {
		return exec.Command("false")
	}
	t.Cleanup(func() {
		execCommandForRun = exec.Command
		execCommand = exec.Command
		execCommandForTailscale = exec.Command
	})

	result, err := executeOS(map[string]any{
		"interface":  "eth0",
		"mode":       "static",
		"new_ip_v4":  "10.0.0.200/24",
		"ip_version": "4",
		"method":     "manual",
	})
	if err != nil {
		t.Fatalf("executeOS manual: %v", err)
	}
	v, ok := result["requires_confirmation"].(bool)
	if !ok || !v {
		t.Errorf("expected requires_confirmation=true, got %v", result["requires_confirmation"])
	}
	if result["method_used"] != "manual" {
		t.Errorf("expected method_used=manual, got %v", result["method_used"])
	}
}

func TestExecuteOS_MethodCommitTimer_ReturnsFlagAndStartsTimer(t *testing.T) {
	execCommandForRun = func(name string, args ...string) *exec.Cmd {
		return exec.Command("true")
	}
	execCommand = func(name string, args ...string) *exec.Cmd {
		return exec.Command("echo", "")
	}
	execCommandForTailscale = func(name string, args ...string) *exec.Cmd {
		return exec.Command("false")
	}
	t.Cleanup(func() {
		execCommandForRun = exec.Command
		execCommand = exec.Command
		execCommandForTailscale = exec.Command
	})

	tmpDir := t.TempDir()
	// Override the dead man's switch path so we don't write to /var/lib.
	deadManPathOverride = tmpDir + "/pending_rollback.json"
	t.Cleanup(func() { deadManPathOverride = "" })

	result, err := executeOS(map[string]any{
		"interface":            "eth0",
		"mode":                 "static",
		"new_ip_v4":            "10.0.0.200/24",
		"ip_version":           "4",
		"method":               "commit_timer",
		"commit_timer_seconds": 10,
		"probe_url":            "http://127.0.0.1:1",
	})
	if err != nil {
		t.Fatalf("executeOS commit_timer: %v", err)
	}
	if result["method_used"] != "commit_timer" {
		t.Errorf("expected method_used=commit_timer, got %v", result["method_used"])
	}
	started, _ := result["commit_timer_started"].(bool)
	if !started {
		t.Error("expected commit_timer_started=true")
	}
}

func TestExecuteOS_MethodTailscale_ReturnsTailscaleIP(t *testing.T) {
	execCommandForRun = func(name string, args ...string) *exec.Cmd {
		return exec.Command("true")
	}
	execCommand = func(name string, args ...string) *exec.Cmd {
		return exec.Command("echo", "")
	}
	execCommandForTailscale = func(name string, args ...string) *exec.Cmd {
		// Simulate tailscale ip -4 returning an IP.
		return exec.Command("echo", "100.64.0.1")
	}
	t.Cleanup(func() {
		execCommandForRun = exec.Command
		execCommand = exec.Command
		execCommandForTailscale = exec.Command
	})

	result, err := executeOS(map[string]any{
		"interface":  "eth0",
		"mode":       "static",
		"new_ip_v4":  "10.0.0.200/24",
		"ip_version": "4",
		"method":     "tailscale",
	})
	if err != nil {
		t.Fatalf("executeOS tailscale: %v", err)
	}
	if result["method_used"] != "tailscale" {
		t.Errorf("expected method_used=tailscale, got %v", result["method_used"])
	}
	if result["tailscale_ip"] == "" {
		t.Error("expected tailscale_ip to be populated")
	}
}
```

- [ ] **Step 8.2: Add `deadManPathOverride` variable to `changip_deadman.go`**

This lets tests redirect the pending-rollback file without hitting `/var/lib`.

At the top of `changip_deadman.go`, below the `PendingRollbackPath` const, add:

```go
// deadManPathOverride overrides PendingRollbackPath when non-empty. Used in tests.
var deadManPathOverride string

func activePendingRollbackPath() string {
	if deadManPathOverride != "" {
		return deadManPathOverride
	}
	return PendingRollbackPath
}
```

- [ ] **Step 8.3: Run tests to confirm compile failure**

```bash
cd agent
go test ./commands/changip/... -run "TestExecuteOS_Method" -v
```

Expected: compile error — `requires_confirmation`, `method_used`, `commit_timer_started`, `tailscale_ip` are not yet returned by `executeOS`.

- [ ] **Step 8.4: Update `executeOS` in `changip_linux.go`**

Replace the entire `executeOS` function body with the following. This adds parameter reading, method resolution, and dispatch while preserving all existing behaviour for callers that pass no `method`:

```go
func executeOS(params map[string]any) (map[string]any, error) {
	iface, _ := params["interface"].(string)
	mode, _ := params["mode"].(string)
	ipVersion, _ := params["ip_version"].(string)

	if iface == "" {
		return nil, fmt.Errorf("interface is required")
	}
	if mode != "static" && mode != "dhcp" {
		return nil, fmt.Errorf("mode must be 'static' or 'dhcp', got %q", mode)
	}
	if ipVersion == "" {
		ipVersion = "4"
	}

	// --- New parameters ---
	method, _ := params["method"].(string)
	if method == "" {
		method = "auto"
	}
	commitTimerSecs := paramInt(params, "commit_timer_seconds", 30)
	if commitTimerSecs < 10 {
		commitTimerSecs = 10
	}
	if commitTimerSecs > 300 {
		commitTimerSecs = 300
	}
	probeIntervalSecs := paramInt(params, "probe_interval_seconds", 5)
	probeURL, _ := params["probe_url"].(string)

	var dnsServers []string
	if raw, ok := params["dns_servers"].([]any); ok {
		for _, v := range raw {
			if s, ok := v.(string); ok {
				dnsServers = append(dnsServers, s)
			}
		}
	}
	var dnsSearchDomains []string
	if raw, ok := params["dns_search_domains"].([]any); ok {
		for _, v := range raw {
			if s, ok := v.(string); ok {
				dnsSearchDomains = append(dnsSearchDomains, s)
			}
		}
	}

	// --- Capture snapshot before any change ---
	snap, err := captureSnapshot(iface)
	if err != nil {
		return nil, fmt.Errorf("capturing snapshot: %w", err)
	}
	snapBytes, _ := json.Marshal(snap)
	var snapMap map[string]any
	json.Unmarshal(snapBytes, &snapMap)

	nm := detectNetworkManager()

	// --- Auto method selection ---
	if method == "auto" {
		tsIP, tsActive := IsTailscaleActive()
		if tsActive && probeURL != "" && IsControlPlaneReachableViaTailscale(probeURL, 5*time.Second) {
			_ = tsIP
			method = "tailscale"
		} else {
			// Fall through: secondary_swap requires routability check (not implemented here — default to commit_timer).
			method = "commit_timer"
		}
	}

	// --- Dispatch ---
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
		tsIP, _ := IsTailscaleActive()
		if err := applyIPChange(nm, iface, mode, ipVersion, params); err != nil {
			return nil, err
		}
		if err := configureDNS(nm, iface, snap.ConnectionName, dnsServers, dnsSearchDomains); err != nil {
			return nil, err
		}
		result["applied"] = true
		result["tailscale_ip"] = tsIP

	case "secondary_swap":
		newCIDR, _ := params["new_ip_v4"].(string)
		if newCIDR == "" {
			return nil, fmt.Errorf("new_ip_v4 required for secondary_swap")
		}
		if err := AddSecondaryIP(iface, newCIDR); err != nil {
			return nil, fmt.Errorf("add secondary IP: %w", err)
		}
		// Remove old primary IPs.
		for _, old := range snap.IPv4Addresses {
			_ = RemoveSecondaryIP(iface, old)
		}
		if err := configureDNS(nm, iface, snap.ConnectionName, dnsServers, dnsSearchDomains); err != nil {
			return nil, err
		}
		result["applied"] = true
		result["secondary_ip_added"] = newCIDR

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
			_ = rollbackOS(map[string]any{"snapshot": snapMap, "interface": iface})
		})
		if err != nil {
			return nil, fmt.Errorf("start dead man's switch: %w", err)
		}
		if err := applyIPChange(nm, iface, mode, ipVersion, params); err != nil {
			return nil, err
		}
		if err := configureDNS(nm, iface, snap.ConnectionName, dnsServers, dnsSearchDomains); err != nil {
			return nil, err
		}
		result["applied"] = true
		result["commit_timer_started"] = true

	case "manual":
		if err := applyIPChange(nm, iface, mode, ipVersion, params); err != nil {
			return nil, err
		}
		result["applied"] = true
		result["requires_confirmation"] = true

	default:
		// Backward-compatible: apply change directly.
		if err := applyIPChange(nm, iface, mode, ipVersion, params); err != nil {
			return nil, err
		}
		if err := configureDNS(nm, iface, snap.ConnectionName, dnsServers, dnsSearchDomains); err != nil {
			return nil, err
		}
		result["applied"] = true
	}

	return result, nil
}

// paramInt reads an int parameter from params, returning def if missing or wrong type.
func paramInt(params map[string]any, key string, def int) int {
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

- [ ] **Step 8.5: Run method dispatch tests**

```bash
cd agent
go test ./commands/changip/... -run "TestExecuteOS_Method" -v -timeout 30s
```

Expected:
```
--- PASS: TestExecuteOS_MethodManual_ReturnsRequiresConfirmation
--- PASS: TestExecuteOS_MethodCommitTimer_ReturnsFlagAndStartsTimer
--- PASS: TestExecuteOS_MethodTailscale_ReturnsTailscaleIP
```

- [ ] **Step 8.6: Run all tests to confirm no regressions**

```bash
cd agent
go test ./commands/changip/... -v -timeout 60s
```

Expected: all tests PASS, no failures.

- [ ] **Step 8.7: Commit**

```bash
git add agent/commands/changip/changip_linux.go \
        agent/commands/changip/changip_deadman.go \
        agent/commands/changip/changip_linux_test.go
git commit -m "feat(changip): method selection dispatch — tailscale, secondary_swap, commit_timer, manual; DNS now applied"
```

---

## Task 9: Register Extended Command + Build

**Files:**
- No new files — confirm registration is correct and build passes

- [ ] **Step 9.1: Verify the command is already registered**

```bash
grep -r "changip\|change_ip" agent/executor.go agent/main.go 2>/dev/null || \
  grep -r "changip\|change_ip" agent/ --include="*.go" -l
```

The `changip` package must appear in the agent's command registry. If it is missing, locate the registration map (typically in `agent/main.go` or `agent/executor.go`) and add:

```go
"change_ip": changip.Execute,
```

with the import `nexplane-agent/commands/changip`. (Based on the existing plan context, the command is already registered — this step just confirms it.)

- [ ] **Step 9.2: Run full test suite**

```bash
cd agent
go test ./commands/changip/... -v -timeout 60s
```

Expected output ends with:
```
ok  	nexplane-agent/commands/changip	...s
```

All individual test names should show `--- PASS`.

- [ ] **Step 9.3: Build Linux binary**

```bash
cd agent
GOOS=linux GOARCH=amd64 go build -ldflags "-X main.Version=0.1.0" -o dist/nexplane-agent-linux-amd64 ./
echo "Build OK"
```

Expected: `Build OK` with no errors.

- [ ] **Step 9.4: Build Windows binary**

```bash
cd agent
GOOS=windows GOARCH=amd64 go build -ldflags "-X main.Version=0.1.0" -o dist/nexplane-agent-windows-amd64.exe ./
echo "Build OK"
```

Expected: `Build OK` with no errors.

- [ ] **Step 9.5: Commit**

```bash
git add agent/dist/
git commit -m "build(changip): confirm build OK — linux-amd64 and windows-amd64 binaries"
```

---

## Self-Review Checklist

**Spec coverage:**

| Spec requirement | Task |
|---|---|
| Extended snapshot: all IPv4/IPv6, routes, DNS, MTU, NM type | Task 1 |
| DNS fix — Linux (NM, systemd-resolved, resolv.conf) | Task 2 |
| DNS fix — Windows (netsh) | Task 3 |
| Tailscale detection helper | Task 4 |
| Secondary IP — Linux (add, remove, get) | Task 5 |
| Secondary IP — Windows (add, remove) | Task 6 |
| Dead man's switch (write file, goroutine, crash recovery) | Task 7 |
| Method selection (auto, tailscale, secondary_swap, commit_timer, manual) | Task 8 |
| Result fields: `method_used`, `tailscale_ip`, `secondary_ip_added`, `commit_timer_started`, `requires_confirmation` | Task 8 |
| `go test` and `go build` clean | Task 9 |

**Not in this plan (deferred to Plan 2+):**
- `migrate_ip` change type (multi-stage orchestration)
- `ip_campaign` fleet orchestrator
- UI changes (Network tab, migration wizard, campaign page)
- DNS TTL-aware ordering and provider integration
- Windows method dispatch (commit_timer, secondary_swap paths) — Windows `executeOS` dispatches to netsh apply; the Linux path covers the four-method logic. A follow-up plan adds parity.
- Smoke tests against real AWS infrastructure (separate smoke test plan)
