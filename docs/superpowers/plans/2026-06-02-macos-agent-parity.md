# macOS Agent Feature Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add macOS implementations for agent commands that currently return errors on darwin, achieving parity with the Linux security posture pipeline.

**Scope decision (2026-06-02):** eBPF is Linux-only — macOS `ebpf_*` command stubs (Tasks 3–6 in original plan) are REMOVED from scope. macOS network/LSM story uses native tools with honest naming:
- Network policy → `macos_pf_policy` (pfctl anchors) — NOT `configure_ebpf_network`
- LSM/auditing → `macos_audit_policy` (BSM audit_control) + `macos_tcc_audit` (read-only TCC inventory)
- Execution control → Santa (already implemented in MAC_AGENT_BOOTSTRAP)

**Architecture:** Each Linux command lives in a `*_linux.go` file with a `*_other.go` `//go:build !linux` stub. We add `*_darwin.go` counterparts and narrow the stub to `//go:build !linux && !darwin`. macOS tools: `dscl`, `sysadminctl`, `pfctl`, `audit`, `softwareupdate`, TCC DB.

**Tech Stack:** Go 1.21+, `os/exec`, build tags `//go:build darwin` / `//go:build !linux && !darwin`.

---

## File Map

**Create:**
- `agent/commands/linuxauth/lock_user_darwin.go` — `LockLocalUserExecute`/`Rollback` via `dscl`/`sysadminctl`
- `agent/commands/ebpf/ebpf_soak_darwin.go` — `EbpfNetworkSoakExecute` (lsof -i), `EbpfLsmSoakExecute` (lsof files)
- `agent/commands/ebpf/ebpf_network_policy_darwin.go` — `ConfigureEbpfNetworkExecute`/`Rollback` via pfctl
- `agent/commands/ebpf/ebpf_lsm_policy_darwin.go` — `ConfigureEbpfLsmExecute`/`Rollback` via BSM audit_control
- `agent/commands/ebpf/ebpf_promote_darwin.go` — `PromoteEbpfPolicyExecute`/`Rollback` (pf / BSM flip)
- `agent/commands/macos/patch_darwin.go` — `AuditMacPatchStatusExecute`, `ApplyMacPatchesExecute`/`Rollback`
- `agent/commands/linuxharden/security_audit_darwin.go` — `authorized_keys`, `sudoers`, `suid_scan`, `ssl_cert_inspect` for darwin
- `agent/commands/linuxharden/lynis_audit_darwin.go` — lynis for darwin

**Modify:**
- `agent/commands/linuxauth/lock_user_other.go` — change tag to `//go:build !linux && !darwin`
- `agent/commands/ebpf/ebpf_soak_other.go` — change tag to `//go:build !linux && !darwin`, add `parseNetstatLine` stub
- `agent/commands/ebpf/ebpf_network_policy_other.go` — change tag to `//go:build !linux && !darwin`
- `agent/commands/ebpf/ebpf_lsm_policy_other.go` — change tag to `//go:build !linux && !darwin`
- `agent/commands/ebpf/ebpf_promote_other.go` — change tag to `//go:build !linux && !darwin`
- `agent/commands/linuxharden/security_audit_other.go` (create) — stubs for non-linux/darwin
- `agent/commands/macos/macos.go` — add `AuditMacPatchStatusExecute`, `ApplyMacPatchesExecute`, `ApplyMacPatchesRollback`
- `agent/commands/macos/macos_other.go` — add stubs for new patch functions
- `agent/executor/executor.go` — register new macOS commands, update `audit_patch_status` router

---

## Task 1: `lock_local_user` for macOS

**Files:**
- Create: `agent/commands/linuxauth/lock_user_darwin.go`
- Modify: `agent/commands/linuxauth/lock_user_other.go`

- [ ] **Step 1: Narrow the existing stub build tag**

In `agent/commands/linuxauth/lock_user_other.go`, change line 1 from:
```go
//go:build !linux
```
to:
```go
//go:build !linux && !darwin
```

- [ ] **Step 2: Create the darwin implementation**

Create `agent/commands/linuxauth/lock_user_darwin.go`:

```go
//go:build darwin

package linuxauth

import (
	"fmt"
	"os/exec"
	"strings"
	"time"
)

func LockLocalUserExecute(params map[string]any) (map[string]any, error) {
	username, _ := params["username"].(string)
	if username == "" {
		return nil, fmt.Errorf("username is required")
	}
	terminateSessions, _ := params["terminate_sessions"].(bool)

	// Snapshot current AuthenticationAuthority for rollback
	snapshotOut, _ := exec.Command("dscl", ".", "-read", "/Users/"+username, "AuthenticationAuthority").Output()
	snapshot := strings.TrimSpace(string(snapshotOut))

	// Disable via sysadminctl (preferred) or dscl fallback
	out, err := exec.Command("sysadminctl", "-disableUser", username).CombinedOutput()
	if err != nil {
		// Fallback: set AuthenticationAuthority directly
		out2, err2 := exec.Command("dscl", ".", "-append", "/Users/"+username,
			"AuthenticationAuthority", ";DisabledUser;").CombinedOutput()
		if err2 != nil {
			return nil, fmt.Errorf("lock user failed: sysadminctl: %s; dscl: %s", out, out2)
		}
	}

	killedSessions := 0
	if terminateSessions {
		exec.Command("pkill", "-KILL", "-u", username).Run() //nolint:errcheck
		killedSessions = 1
	}

	return map[string]any{
		"username":        username,
		"locked":          true,
		"sessions_killed": killedSessions,
		"snapshot":        snapshot,
		"locked_at":       time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func LockLocalUserRollback(params map[string]any) (map[string]any, error) {
	username, _ := params["username"].(string)
	if username == "" {
		return nil, fmt.Errorf("username is required for rollback")
	}

	out, err := exec.Command("sysadminctl", "-enableUser", username).CombinedOutput()
	if err != nil {
		// Fallback: remove DisabledUser marker via dscl
		out2, err2 := exec.Command("dscl", ".", "-delete", "/Users/"+username,
			"AuthenticationAuthority", ";DisabledUser;").CombinedOutput()
		if err2 != nil {
			return nil, fmt.Errorf("unlock failed: sysadminctl: %s; dscl: %s", out, out2)
		}
	}
	return map[string]any{"rolled_back": true, "username": username}, nil
}
```

- [ ] **Step 3: Verify it compiles**

```bash
cd agent && GOOS=darwin GOARCH=arm64 go build ./commands/linuxauth/... 2>&1
```
Expected: no output (success)

- [ ] **Step 4: Commit**

```bash
git add agent/commands/linuxauth/lock_user_darwin.go agent/commands/linuxauth/lock_user_other.go
git commit -m "feat(agent/macos): lock_local_user via sysadminctl/dscl"
```

---

## Task 2: macOS patch audit + apply

**Files:**
- Create: `agent/commands/macos/patch_darwin.go`
- Modify: `agent/commands/macos/macos.go`
- Modify: `agent/commands/macos/macos_other.go`

- [ ] **Step 1: Create `patch_darwin.go`**

Create `agent/commands/macos/patch_darwin.go`:

```go
//go:build darwin

package macos

import (
	"fmt"
	"os/exec"
	"strings"
	"time"
)

func auditMacPatchStatus(_ map[string]any) (map[string]any, error) {
	out, err := exec.Command("softwareupdate", "-l").CombinedOutput()
	raw := strings.TrimSpace(string(out))
	// softwareupdate exits 1 when updates are available; treat as success
	_ = err

	var updates []map[string]any
	for _, line := range strings.Split(raw, "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "*") || strings.HasPrefix(line, "-") {
			label := strings.TrimLeft(line, "*- ")
			isSecurity := strings.Contains(strings.ToLower(label), "security")
			updates = append(updates, map[string]any{
				"label":    strings.TrimSpace(label),
				"security": isSecurity,
			})
		}
	}

	return map[string]any{
		"update_count":    len(updates),
		"security_count":  countSecurity(updates),
		"updates":         updates,
		"raw_output":      raw,
		"audited_at":      time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func countSecurity(updates []map[string]any) int {
	n := 0
	for _, u := range updates {
		if sec, _ := u["security"].(bool); sec {
			n++
		}
	}
	return n
}

func applyMacPatches(params map[string]any) (map[string]any, error) {
	mode, _ := params["mode"].(string)
	if mode == "" {
		mode = "recommended"
	}

	// Snapshot: list installed packages for rollback reference
	listOut, _ := exec.Command("softwareupdate", "-l").CombinedOutput()
	snapshot := strings.TrimSpace(string(listOut))

	var args []string
	switch mode {
	case "all":
		args = []string{"--install", "--all"}
	case "recommended", "security_only":
		args = []string{"--install", "--recommended"}
	default:
		return nil, fmt.Errorf("mode must be recommended, all, or security_only; got %q", mode)
	}

	out, err := exec.Command("softwareupdate", args...).CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("softwareupdate install failed: %s: %w", out, err)
	}

	installed := parseInstalled(string(out))
	return map[string]any{
		"mode":               mode,
		"installed_count":    len(installed),
		"installed_packages": installed,
		"snapshot":           snapshot,
		"applied_at":         time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func parseInstalled(out string) []string {
	var pkgs []string
	for _, line := range strings.Split(out, "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "Installing:") {
			pkgs = append(pkgs, strings.TrimSpace(strings.TrimPrefix(line, "Installing:")))
		}
	}
	return pkgs
}

func applyMacPatchesRollback(_ map[string]any) (map[string]any, error) {
	// macOS softwareupdate has no uninstall mechanism; rollback is informational
	return map[string]any{
		"rolled_back": false,
		"reason":      "macOS softwareupdate does not support uninstall; manual rollback required",
	}, nil
}
```

- [ ] **Step 2: Add exported wrappers to `macos.go`**

Append to `agent/commands/macos/macos.go`:

```go
// AuditMacPatchStatusExecute lists available macOS software updates.
func AuditMacPatchStatusExecute(params map[string]any) (map[string]any, error) {
	return auditMacPatchStatus(params)
}

// ApplyMacPatchesExecute installs macOS software updates via softwareupdate.
func ApplyMacPatchesExecute(params map[string]any) (map[string]any, error) {
	return applyMacPatches(params)
}

// ApplyMacPatchesRollback is informational — macOS patches cannot be auto-uninstalled.
func ApplyMacPatchesRollback(params map[string]any) (map[string]any, error) {
	return applyMacPatchesRollback(params)
}
```

- [ ] **Step 3: Add stubs to `macos_other.go`**

Append to `agent/commands/macos/macos_other.go`:

```go
func auditMacPatchStatus(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("audit_mac_patch_status is only supported on macOS")
}

func applyMacPatches(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("apply_mac_patches is only supported on macOS")
}

func applyMacPatchesRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"rolled_back": false, "reason": "apply_mac_patches is only supported on macOS"}, nil
}
```

- [ ] **Step 4: Verify compilation**

```bash
cd agent && GOOS=darwin GOARCH=arm64 go build ./commands/macos/... 2>&1
```
Expected: no output

- [ ] **Step 5: Commit**

```bash
git add agent/commands/macos/patch_darwin.go agent/commands/macos/macos.go agent/commands/macos/macos_other.go
git commit -m "feat(agent/macos): audit_mac_patch_status and apply_mac_patches via softwareupdate"
```

---

## ~~Task 3: `ebpf_network_soak` and `ebpf_lsm_soak` for darwin~~ REMOVED — eBPF is Linux-only; macOS equivalent is `macos_pf_policy` (see Task 10)

**Files:**
- Create: `agent/commands/ebpf/ebpf_soak_darwin.go`
- Modify: `agent/commands/ebpf/ebpf_soak_other.go`

- [ ] **Step 1: Narrow the other stub**

In `agent/commands/ebpf/ebpf_soak_other.go`, change line 1 from:
```go
//go:build !linux
```
to:
```go
//go:build !linux && !darwin
```

- [ ] **Step 2: Create `ebpf_soak_darwin.go`**

Create `agent/commands/ebpf/ebpf_soak_darwin.go`:

```go
//go:build darwin

package ebpf

import (
	"fmt"
	"os/exec"
	"strconv"
	"strings"
	"time"
)

// EbpfNetworkSoakExecute observes outbound network flows using lsof -i.
// Returns flows with {dst_ip, dst_port (int), protocol, process} matching
// the ebpf_network synthesizer contract.
func EbpfNetworkSoakExecute(params map[string]any) (map[string]any, error) {
	dur, ok := params["duration_seconds"]
	if !ok {
		return nil, fmt.Errorf("duration_seconds is required")
	}
	seconds := int(toFloat(dur))
	if seconds <= 0 {
		seconds = 30
	}

	seen := map[string]bool{}
	var flows []map[string]any

	deadline := time.Now().Add(time.Duration(seconds) * time.Second)
	for time.Now().Before(deadline) {
		// -i: network files, -n: no hostname resolution, -P: no port->name, -F: field output
		out, err := exec.Command("lsof", "-i", "-n", "-P").Output()
		if err != nil {
			time.Sleep(2 * time.Second)
			continue
		}
		for _, line := range strings.Split(string(out), "\n") {
			f := parseLsofNetLine(line)
			if f == nil {
				continue
			}
			key := fmt.Sprintf("%s|%v|%s", f["dst_ip"], f["dst_port"], f["protocol"])
			if !seen[key] {
				seen[key] = true
				flows = append(flows, f)
			}
		}
		time.Sleep(2 * time.Second)
	}

	return map[string]any{
		"flows":       flows,
		"flow_count":  len(flows),
		"duration":    seconds,
		"observed_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func EbpfNetworkSoakRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"rolled_back": false, "reason": "soak is read-only"}, nil
}

// EbpfLsmSoakExecute collects file-access events by scanning open files
// across all processes via lsof, returning {syscall, path, process} events
// matching the ebpf_lsm synthesizer contract.
func EbpfLsmSoakExecute(params map[string]any) (map[string]any, error) {
	dur, ok := params["duration_seconds"]
	if !ok {
		return nil, fmt.Errorf("duration_seconds is required")
	}
	seconds := int(toFloat(dur))
	if seconds <= 0 {
		seconds = 30
	}

	seen := map[string]bool{}
	var events []map[string]any
	interval := time.Duration(seconds/3+1) * time.Second
	deadline := time.Now().Add(time.Duration(seconds) * time.Second)

	for time.Now().Before(deadline) {
		collectLsofEvents(seen, &events)
		time.Sleep(interval)
	}
	collectLsofEvents(seen, &events)

	return map[string]any{
		"events":      events,
		"event_count": len(events),
		"duration":    seconds,
		"observed_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func EbpfLsmSoakRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"rolled_back": false, "reason": "soak is read-only"}, nil
}

// collectLsofEvents scans all open files via lsof to build {syscall, path, process} events.
func collectLsofEvents(seen map[string]bool, events *[]map[string]any) {
	// -n: no hostname, +D /: all files (too slow), use -F for field output per process
	out, err := exec.Command("lsof", "-n", "-F", "pcn").Output()
	if err != nil {
		return
	}

	var comm, pid string
	for _, line := range strings.Split(string(out), "\n") {
		if len(line) < 2 {
			continue
		}
		switch line[0] {
		case 'p':
			pid = line[1:]
			_ = pid
		case 'c':
			comm = line[1:]
		case 'n':
			path := line[1:]
			if !strings.HasPrefix(path, "/") {
				continue
			}
			// skip pseudo paths
			if strings.HasPrefix(path, "/dev") || strings.HasPrefix(path, "/proc") {
				continue
			}
			syscall := "read"
			if strings.HasSuffix(path, ".dylib") || strings.HasSuffix(path, ".so") {
				syscall = "mmap"
			}
			key := fmt.Sprintf("%s|%s|%s", syscall, path, comm)
			if !seen[key] {
				seen[key] = true
				*events = append(*events, map[string]any{
					"syscall": syscall,
					"path":    path,
					"process": comm,
				})
			}
		}
	}
}

// parseLsofNetLine parses a line from `lsof -i -n -P` output.
// Format: COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME
// NAME field for TCP/UDP looks like: 10.0.1.5:54321->54.239.17.53:443 (ESTABLISHED)
func parseLsofNetLine(line string) map[string]any {
	fields := strings.Fields(line)
	if len(fields) < 9 {
		return nil
	}
	typeField := fields[4]
	if typeField != "IPv4" && typeField != "IPv6" {
		return nil
	}
	name := fields[8]
	if !strings.Contains(name, "->") {
		return nil // LISTEN or unconnected
	}

	// Extract protocol from FD field or detect from context
	proto := "tcp"
	if strings.Contains(strings.ToUpper(fields[7]), "UDP") {
		proto = "udp"
	}
	// Node field (fields[7]) might say "TCP" or "UDP"
	nodeLower := strings.ToLower(fields[7])
	if strings.Contains(nodeLower, "udp") {
		proto = "udp"
	}

	// name: localIP:localPort->dstIP:dstPort (STATE)
	parts := strings.SplitN(name, "->", 2)
	if len(parts) != 2 {
		return nil
	}
	remote := strings.Fields(parts[1])[0] // strip "(ESTABLISHED)"

	dstIP, dstPort := splitDarwinAddrPort(remote)
	if dstPort == 0 {
		return nil
	}

	process := fields[0]
	return map[string]any{
		"dst_ip":   dstIP,
		"dst_port": dstPort,
		"protocol": proto,
		"process":  process,
	}
}

func splitDarwinAddrPort(s string) (string, int) {
	// Handle IPv6 [::1]:port
	if strings.HasPrefix(s, "[") {
		close := strings.LastIndex(s, "]")
		if close < 0 {
			return s, 0
		}
		ip := s[1:close]
		rest := s[close+1:]
		if len(rest) > 1 && rest[0] == ':' {
			port, err := strconv.Atoi(rest[1:])
			if err != nil {
				return ip, 0
			}
			return ip, port
		}
		return ip, 0
	}
	idx := strings.LastIndex(s, ":")
	if idx < 0 {
		return s, 0
	}
	port, err := strconv.Atoi(s[idx+1:])
	if err != nil {
		return s[:idx], 0
	}
	return s[:idx], port
}

func toFloat(v any) float64 {
	switch x := v.(type) {
	case float64:
		return x
	case int:
		return float64(x)
	case int64:
		return float64(x)
	}
	return 0
}
```

- [ ] **Step 3: Verify compilation**

```bash
cd agent && GOOS=darwin GOARCH=arm64 go build ./commands/ebpf/... 2>&1
```
Expected: no output

- [ ] **Step 4: Commit**

```bash
git add agent/commands/ebpf/ebpf_soak_darwin.go agent/commands/ebpf/ebpf_soak_other.go
git commit -m "feat(agent/macos): ebpf_network_soak and ebpf_lsm_soak via lsof"
```

---

## ~~Task 4: `configure_ebpf_network` for darwin~~ REMOVED — replaced by `macos_pf_policy` (see Task 10)

**Files:**
- Create: `agent/commands/ebpf/ebpf_network_policy_darwin.go`
- Modify: `agent/commands/ebpf/ebpf_network_policy_other.go` (build tag only)

- [ ] **Step 1: Narrow the stub build tag**

In `agent/commands/ebpf/ebpf_network_policy_other.go`, change the build tag from:
```go
//go:build !linux
```
to:
```go
//go:build !linux && !darwin
```

(This file currently has stubs `ConfigureEbpfNetworkExecute`/`Rollback` that return `fmt.Errorf("requires Linux")`. Verify the file has those stubs and just change the tag.)

- [ ] **Step 2: Create `ebpf_network_policy_darwin.go`**

Create `agent/commands/ebpf/ebpf_network_policy_darwin.go`:

```go
//go:build darwin

package ebpf

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

const (
	pfAnchorName = "nexplane_net"
	pfAnchorFile = "/etc/pf.anchors/nexplane_net"
	pfSnapshotDir = "/etc/nexplane/pf"
	pfModeFile    = "/etc/nexplane/pf/ebpf_network-mode"
	pfConfLine    = `anchor "nexplane_net"` + "\n" + `load anchor "nexplane_net" from "/etc/pf.anchors/nexplane_net"`
)

func ConfigureEbpfNetworkExecute(params map[string]any) (map[string]any, error) {
	mode, _ := params["mode"].(string)
	if mode == "" {
		return nil, fmt.Errorf("mode is required (audit or enforce)")
	}
	if mode != "audit" && mode != "enforce" {
		return nil, fmt.Errorf("mode must be 'audit' or 'enforce'")
	}

	os.MkdirAll(pfSnapshotDir, 0755) //nolint:errcheck
	os.MkdirAll("/etc/pf.anchors", 0755) //nolint:errcheck

	// Snapshot current pf rules
	snapshotPath := fmt.Sprintf("%s/pf-snapshot-%d.txt", pfSnapshotDir, time.Now().UnixNano())
	snapshotOut, _ := exec.Command("pfctl", "-s", "rules").Output()
	os.WriteFile(snapshotPath, snapshotOut, 0600) //nolint:errcheck

	// Ensure anchor is referenced in pf.conf
	ensurePfAnchor()

	profile, _ := params["profile"].(map[string]any)
	flows, _ := profile["flows"].([]any)

	var rules []string
	rulesAdded := 0
	for _, f := range flows {
		flow, ok := f.(map[string]any)
		if !ok {
			continue
		}
		proto, _ := flow["protocol"].(string)
		if proto == "" {
			proto, _ = flow["proto"].(string)
		}
		var port string
		switch v := flow["dst_port"].(type) {
		case float64:
			port = fmt.Sprintf("%d", int(v))
		case int:
			port = fmt.Sprintf("%d", v)
		case string:
			port = v
		}
		if proto == "" || port == "" || port == "0" {
			continue
		}
		if mode == "audit" {
			rules = append(rules, fmt.Sprintf("pass log out proto %s to any port %s", proto, port))
		} else {
			rules = append(rules, fmt.Sprintf("pass out proto %s to any port %s", proto, port))
		}
		rulesAdded++
	}

	if mode == "enforce" && rulesAdded > 0 {
		rules = append(rules, "block return out all")
	}

	rulesContent := strings.Join(rules, "\n") + "\n"
	if err := os.WriteFile(pfAnchorFile, []byte(rulesContent), 0644); err != nil {
		return nil, fmt.Errorf("writing pf anchor: %w", err)
	}

	// Reload pf
	exec.Command("pfctl", "-f", "/etc/pf.conf").Run() //nolint:errcheck
	exec.Command("pfctl", "-e").Run()                  //nolint:errcheck

	os.WriteFile(pfModeFile, []byte(mode), 0644) //nolint:errcheck

	return map[string]any{
		"mode":        mode,
		"rules_added": rulesAdded,
		"anchor":      pfAnchorName,
		"snapshot_id": snapshotPath,
		"applied_at":  time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func ConfigureEbpfNetworkRollback(params map[string]any) (map[string]any, error) {
	snapshotID, _ := params["snapshot_id"].(string)

	// Remove anchor file and flush anchor
	os.Remove(pfAnchorFile)                                        //nolint:errcheck
	exec.Command("pfctl", "-a", pfAnchorName, "-F", "all").Run()  //nolint:errcheck

	if snapshotID != "" {
		data, err := os.ReadFile(snapshotID)
		if err == nil {
			tmp, _ := os.CreateTemp("", "nexplane-pf-restore-*.conf")
			tmp.Write(data) //nolint:errcheck
			tmp.Close()
			exec.Command("pfctl", "-f", tmp.Name()).Run() //nolint:errcheck
			os.Remove(tmp.Name())
		}
	}

	os.WriteFile(pfModeFile, []byte(""), 0644) //nolint:errcheck
	return map[string]any{"rolled_back": true}, nil
}

// ensurePfAnchor adds the nexplane_net anchor to /etc/pf.conf if not already present.
func ensurePfAnchor() {
	data, _ := os.ReadFile("/etc/pf.conf")
	if strings.Contains(string(data), pfAnchorName) {
		return
	}
	f, err := os.OpenFile("/etc/pf.conf", os.O_APPEND|os.O_WRONLY, 0644)
	if err != nil {
		return
	}
	defer f.Close()
	fmt.Fprintf(f, "\n%s\n", pfConfLine)
}
```

- [ ] **Step 3: Check and create `ebpf_network_policy_other.go` if missing**

If `agent/commands/ebpf/ebpf_network_policy_other.go` does not exist, create it:

```go
//go:build !linux && !darwin

package ebpf

import "fmt"

func ConfigureEbpfNetworkExecute(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_ebpf_network requires Linux or macOS")
}

func ConfigureEbpfNetworkRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"rolled_back": false, "reason": "configure_ebpf_network requires Linux or macOS"}, nil
}
```

- [ ] **Step 4: Verify compilation**

```bash
cd agent && GOOS=darwin GOARCH=arm64 go build ./commands/ebpf/... 2>&1
```
Expected: no output

- [ ] **Step 5: Commit**

```bash
git add agent/commands/ebpf/ebpf_network_policy_darwin.go agent/commands/ebpf/ebpf_network_policy_other.go
git commit -m "feat(agent/macos): configure_ebpf_network via pf anchor"
```

---

## ~~Task 5: `configure_ebpf_lsm` for darwin~~ REMOVED — replaced by `macos_audit_policy` (BSM) + `macos_tcc_audit` (see Task 10)

**Files:**
- Create: `agent/commands/ebpf/ebpf_lsm_policy_darwin.go`
- Modify: `agent/commands/ebpf/ebpf_lsm_policy_other.go` (build tag)

- [ ] **Step 1: Narrow the stub build tag**

In `agent/commands/ebpf/ebpf_lsm_policy_other.go`, change the build tag from:
```go
//go:build !linux
```
to:
```go
//go:build !linux && !darwin
```

- [ ] **Step 2: Create `ebpf_lsm_policy_darwin.go`**

Create `agent/commands/ebpf/ebpf_lsm_policy_darwin.go`:

```go
//go:build darwin

package ebpf

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

const (
	bsmAuditControl = "/etc/security/audit_control"
	bsmModeFile     = "/etc/nexplane/pf/ebpf_lsm-mode"
	bsmSnapshotDir  = "/etc/nexplane/pf"
)

func ConfigureEbpfLsmExecute(params map[string]any) (map[string]any, error) {
	mode, _ := params["mode"].(string)
	if mode == "" {
		return nil, fmt.Errorf("mode is required (audit or enforce)")
	}
	if mode != "audit" && mode != "enforce" {
		return nil, fmt.Errorf("mode must be 'audit' or 'enforce'")
	}

	os.MkdirAll(bsmSnapshotDir, 0755) //nolint:errcheck

	// Snapshot current audit_control
	snapshotPath := fmt.Sprintf("%s/bsm-snapshot-%d.conf", bsmSnapshotDir, time.Now().UnixNano())
	existing, _ := os.ReadFile(bsmAuditControl)
	os.WriteFile(snapshotPath, existing, 0600) //nolint:errcheck

	profile, _ := params["profile"].(map[string]any)
	rules, _ := profile["rules"].([]any)

	// Collect audit event classes from syscalls
	classFlags := buildBSMFlags(rules, mode)

	// Read current audit_control and update the flags: line
	content := string(existing)
	lines := strings.Split(content, "\n")
	var newLines []string
	flagsSet := false
	for _, line := range lines {
		if strings.HasPrefix(line, "flags:") {
			newLines = append(newLines, "flags:"+classFlags)
			flagsSet = true
		} else {
			newLines = append(newLines, line)
		}
	}
	if !flagsSet {
		newLines = append(newLines, "flags:"+classFlags)
	}

	newContent := strings.Join(newLines, "\n")
	if err := os.WriteFile(bsmAuditControl, []byte(newContent), 0644); err != nil {
		return nil, fmt.Errorf("writing audit_control: %w", err)
	}

	// Reload BSM audit daemon
	exec.Command("audit", "-s").Run() //nolint:errcheck

	// Detect active LSM
	kernelLSM := detectDarwinLSM()

	os.WriteFile(bsmModeFile, []byte(mode), 0644) //nolint:errcheck

	return map[string]any{
		"mode":        mode,
		"rules_count": len(rules),
		"snapshot_id": snapshotPath,
		"kernel_lsm":  kernelLSM,
		"applied_at":  time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func ConfigureEbpfLsmRollback(params map[string]any) (map[string]any, error) {
	snapshotID, _ := params["snapshot_id"].(string)
	if snapshotID != "" {
		if data, err := os.ReadFile(snapshotID); err == nil {
			os.WriteFile(bsmAuditControl, data, 0644) //nolint:errcheck
		}
	}
	exec.Command("audit", "-s").Run()                        //nolint:errcheck
	os.WriteFile(bsmModeFile, []byte(""), 0644) //nolint:errcheck
	return map[string]any{"rolled_back": true}, nil
}

// buildBSMFlags maps syscall names to BSM audit class flags.
// audit mode: lo,aa,fd,fm (login, auth, file-delete, file-modify)
// enforce mode: adds fc (file-create), -all (exclude noise)
func buildBSMFlags(rules []any, mode string) string {
	if mode == "enforce" {
		return "lo,aa,ad,fd,fm,fc,-all"
	}
	return "lo,aa,fd,fm"
}

// detectDarwinLSM returns the active macOS security framework info.
func detectDarwinLSM() string {
	// Check SIP status
	out, err := exec.Command("csrutil", "status").Output()
	if err == nil {
		status := strings.TrimSpace(string(out))
		if strings.Contains(status, "enabled") {
			return "sip+bsm"
		}
	}
	return "bsm"
}
```

- [ ] **Step 3: Check and create `ebpf_lsm_policy_other.go` if needed**

If `agent/commands/ebpf/ebpf_lsm_policy_other.go` does not exist, create it:

```go
//go:build !linux && !darwin

package ebpf

import "fmt"

func ConfigureEbpfLsmExecute(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_ebpf_lsm requires Linux or macOS")
}

func ConfigureEbpfLsmRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"rolled_back": false, "reason": "configure_ebpf_lsm requires Linux or macOS"}, nil
}
```

- [ ] **Step 4: Verify**

```bash
cd agent && GOOS=darwin GOARCH=arm64 go build ./commands/ebpf/... 2>&1
```
Expected: no output

- [ ] **Step 5: Commit**

```bash
git add agent/commands/ebpf/ebpf_lsm_policy_darwin.go agent/commands/ebpf/ebpf_lsm_policy_other.go
git commit -m "feat(agent/macos): configure_ebpf_lsm via BSM audit_control"
```

---

## ~~Task 6: `promote_ebpf_policy` for darwin~~ REMOVED — no macOS equivalent; policy promotion handled per-tool (pf/BSM individually)

**Files:**
- Create: `agent/commands/ebpf/ebpf_promote_darwin.go`
- Modify: `agent/commands/ebpf/ebpf_promote_other.go` (build tag)

- [ ] **Step 1: Narrow the stub build tag**

In `agent/commands/ebpf/ebpf_promote_other.go`, change:
```go
//go:build !linux
```
to:
```go
//go:build !linux && !darwin
```

- [ ] **Step 2: Create `ebpf_promote_darwin.go`**

Create `agent/commands/ebpf/ebpf_promote_darwin.go`:

```go
//go:build darwin

package ebpf

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

func PromoteEbpfPolicyExecute(params map[string]any) (map[string]any, error) {
	policyType, _ := params["policy_type"].(string)
	if policyType == "" {
		return nil, fmt.Errorf("policy_type is required (ebpf_network or ebpf_lsm)")
	}
	targetMode, _ := params["target_mode"].(string)
	if targetMode == "" {
		targetMode = "enforce"
	}

	// Read prior mode
	modeFile := darwinModeFileFor(policyType)
	priorMode := "audit"
	if data, err := os.ReadFile(modeFile); err == nil {
		if m := strings.TrimSpace(string(data)); m != "" {
			priorMode = m
		}
	}

	// Snapshot current state
	var snapshot string
	if policyType == "ebpf_network" {
		snapshotOut, _ := exec.Command("pfctl", "-s", "rules").Output()
		snapshot = string(snapshotOut)

		if targetMode == "enforce" {
			// Replace pass log rules with pass + add block-all
			flipPfAnchorToEnforce()
		} else {
			flipPfAnchorToAudit()
		}
	} else if policyType == "ebpf_lsm" {
		snapshotOut, _ := os.ReadFile(bsmAuditControl)
		snapshot = string(snapshotOut)

		if targetMode == "enforce" {
			updateBSMFlags("lo,aa,ad,fd,fm,fc,-all")
		} else {
			updateBSMFlags("lo,aa,fd,fm")
		}
		exec.Command("audit", "-s").Run() //nolint:errcheck
	}

	os.WriteFile(modeFile, []byte(targetMode), 0644) //nolint:errcheck

	return map[string]any{
		"policy_type": policyType,
		"prior_mode":  priorMode,
		"target_mode": targetMode,
		"snapshot":    snapshot,
		"promoted_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func PromoteEbpfPolicyRollback(params map[string]any) (map[string]any, error) {
	policyType, _ := params["policy_type"].(string)
	priorMode, _ := params["prior_mode"].(string)
	snapshot, _ := params["snapshot"].(string)

	if policyType == "ebpf_network" && snapshot != "" {
		tmp, _ := os.CreateTemp("", "nexplane-pf-rollback-*.conf")
		tmp.WriteString(snapshot) //nolint:errcheck
		tmp.Close()
		exec.Command("pfctl", "-f", tmp.Name()).Run() //nolint:errcheck
		os.Remove(tmp.Name())
	} else if policyType == "ebpf_lsm" && snapshot != "" {
		os.WriteFile(bsmAuditControl, []byte(snapshot), 0644) //nolint:errcheck
		exec.Command("audit", "-s").Run()                     //nolint:errcheck
	}

	if policyType != "" && priorMode != "" {
		modeFile := darwinModeFileFor(policyType)
		os.WriteFile(modeFile, []byte(priorMode), 0644) //nolint:errcheck
	}

	return map[string]any{"rolled_back": true, "restored_mode": priorMode}, nil
}

func darwinModeFileFor(policyType string) string {
	return fmt.Sprintf("/etc/nexplane/pf/%s-mode", policyType)
}

func flipPfAnchorToEnforce() {
	data, err := os.ReadFile(pfAnchorFile)
	if err != nil {
		return
	}
	lines := strings.Split(string(data), "\n")
	var newLines []string
	for _, line := range lines {
		newLines = append(newLines, strings.Replace(line, "pass log out", "pass out", 1))
	}
	// Ensure block-all rule exists
	content := strings.Join(newLines, "\n")
	if !strings.Contains(content, "block return out all") {
		content += "\nblock return out all\n"
	}
	os.WriteFile(pfAnchorFile, []byte(content), 0644) //nolint:errcheck
	exec.Command("pfctl", "-f", "/etc/pf.conf").Run() //nolint:errcheck
}

func flipPfAnchorToAudit() {
	data, err := os.ReadFile(pfAnchorFile)
	if err != nil {
		return
	}
	lines := strings.Split(string(data), "\n")
	var newLines []string
	for _, line := range lines {
		if strings.TrimSpace(line) == "block return out all" {
			continue
		}
		newLines = append(newLines, strings.Replace(line, "pass out proto", "pass log out proto", 1))
	}
	os.WriteFile(pfAnchorFile, []byte(strings.Join(newLines, "\n")), 0644) //nolint:errcheck
	exec.Command("pfctl", "-f", "/etc/pf.conf").Run()                      //nolint:errcheck
}

func updateBSMFlags(flags string) {
	data, _ := os.ReadFile(bsmAuditControl)
	lines := strings.Split(string(data), "\n")
	var newLines []string
	flagsSet := false
	for _, line := range lines {
		if strings.HasPrefix(line, "flags:") {
			newLines = append(newLines, "flags:"+flags)
			flagsSet = true
		} else {
			newLines = append(newLines, line)
		}
	}
	if !flagsSet {
		newLines = append(newLines, "flags:"+flags)
	}
	os.WriteFile(bsmAuditControl, []byte(strings.Join(newLines, "\n")), 0644) //nolint:errcheck
}
```

- [ ] **Step 3: Verify**

```bash
cd agent && GOOS=darwin GOARCH=arm64 go build ./commands/ebpf/... 2>&1
```
Expected: no output

- [ ] **Step 4: Commit**

```bash
git add agent/commands/ebpf/ebpf_promote_darwin.go agent/commands/ebpf/ebpf_promote_other.go
git commit -m "feat(agent/macos): promote_ebpf_policy via pf anchor / BSM audit flip"
```

---

## Task 7: Cross-platform POSIX audit commands

These commands use standard POSIX tools available on both Linux and macOS (`find`, `openssl`, `lynis`). Currently they have `//go:build linux` tags. We split them to `//go:build linux || darwin`.

**Files:**
- Modify: `agent/commands/linuxharden/security_audit_linux.go` — change tag to `//go:build linux || darwin`, fix home dir path
- Modify: `agent/commands/linuxharden/lynis_audit_linux.go` — change tag to `//go:build linux || darwin`
- Modify: `agent/commands/linuxharden/security_audit_other.go` (create) — stubs for `!linux && !darwin`
- Modify: `agent/commands/linuxharden/linuxharden_other.go` — remove entries now covered by `linux || darwin`

- [ ] **Step 1: Update `security_audit_linux.go` build tag and home dir**

In `agent/commands/linuxharden/security_audit_linux.go`:

Change line 1 from:
```go
//go:build linux
```
to:
```go
//go:build linux || darwin
```

Find the `authorizedKeysAuditExecute` function. After `homes, _ := filepath.Glob("/home/*")`, add macOS `/Users/*`:
```go
func authorizedKeysAuditExecute(params map[string]any) (map[string]any, error) {
	var entries []map[string]any
	homes, _ := filepath.Glob("/home/*")
	macHomes, _ := filepath.Glob("/Users/*")
	homes = append(homes, macHomes...)
	homes = append(homes, "/root")
	// ... rest unchanged
```

- [ ] **Step 2: Update `lynis_audit_linux.go` build tag**

In `agent/commands/linuxharden/lynis_audit_linux.go`, change line 1 from:
```go
//go:build linux
```
to:
```go
//go:build linux || darwin
```

No other changes needed — `lynis` invocation is identical on macOS.

- [ ] **Step 3: Create `security_audit_other.go` for non-linux/darwin**

Check `agent/commands/linuxharden/security_audit_other.go`. If it doesn't exist, create it:

```go
//go:build !linux && !darwin

package linuxharden

import "fmt"

func authorizedKeysAuditExecute(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("authorized_keys_audit requires Linux or macOS")
}

func authorizedKeysAuditRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"action": "authorized_keys_audit_rollback", "status": "read_only"}, nil
}

func sudoersAuditExecute(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("sudoers_audit requires Linux or macOS")
}

func sudoersAuditRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"action": "sudoers_audit_rollback", "status": "read_only"}, nil
}

func suidScanExecute(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("suid_scan requires Linux or macOS")
}

func suidScanRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"action": "suid_scan_rollback", "status": "read_only"}, nil
}

func sslCertInspectExecute(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("ssl_cert_inspect requires Linux or macOS")
}

func sslCertInspectRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"action": "ssl_cert_inspect_rollback", "status": "read_only"}, nil
}

func lynisAuditExecute(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("lynis_audit requires Linux or macOS")
}

func lynisAuditRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"action": "lynis_audit_rollback", "status": "read_only"}, nil
}
```

- [ ] **Step 4: Check `linuxharden_other.go` for duplicate stubs**

Read `agent/commands/linuxharden/linuxharden_other.go`. If it has `lynisAuditExecute`, `authorizedKeysAuditExecute`, etc. stubs with `//go:build !linux`, those will conflict with the new `security_audit_other.go`. Remove the duplicate function bodies from `linuxharden_other.go` (keep the file but delete the functions that are now in `security_audit_other.go`), and update its build tag to `//go:build !linux && !darwin` as well.

- [ ] **Step 5: Verify**

```bash
cd agent && GOOS=darwin GOARCH=arm64 go build ./commands/linuxharden/... 2>&1
cd agent && GOOS=linux GOARCH=amd64 go build ./commands/linuxharden/... 2>&1
```
Expected: no output for either

- [ ] **Step 6: Commit**

```bash
git add agent/commands/linuxharden/
git commit -m "feat(agent/macos): authorized_keys, sudoers, suid_scan, ssl_cert, lynis work on darwin"
```

---

## Task 8: Register all new macOS commands in executor.go

**Files:**
- Modify: `agent/executor/executor.go`

- [ ] **Step 1: Update `audit_patch_status` router to include darwin**

In `agent/executor/executor.go`, find the `audit_patch_status` entry (around line 144) and change it from:

```go
"audit_patch_status": func(params map[string]any) (map[string]any, error) {
    if runtime.GOOS == "windows" {
        return winpatch.AuditWindowsPatchStatusExecute(params)
    }
    return linuxpatch.AuditLinuxPatchStatusExecute(params)
},
```

to:

```go
"audit_patch_status": func(params map[string]any) (map[string]any, error) {
    switch runtime.GOOS {
    case "windows":
        return winpatch.AuditWindowsPatchStatusExecute(params)
    case "darwin":
        return macos.AuditMacPatchStatusExecute(params)
    default:
        return linuxpatch.AuditLinuxPatchStatusExecute(params)
    }
},
```

- [ ] **Step 2: Add new macOS patch command registrations**

In `agent/executor/executor.go`, after the `"filevault_status"` block (around line 151), add:

```go
// macOS patching
"audit_mac_patch_status": macos.AuditMacPatchStatusExecute,
"apply_mac_patches":      macos.ApplyMacPatchesExecute,
```

- [ ] **Step 3: Add rollback registrations**

In the rollbacks section of `executor.go` (after the existing macOS rollbacks around line 248), add:

```go
"apply_mac_patches": macos.ApplyMacPatchesRollback,
```

- [ ] **Step 4: Verify full build for darwin and linux**

```bash
cd agent && GOOS=darwin GOARCH=arm64 go build . 2>&1
cd agent && GOOS=linux GOARCH=amd64 go build . 2>&1
```
Expected: no output for either

- [ ] **Step 5: Commit**

```bash
git add agent/executor/executor.go
git commit -m "feat(agent): register macOS patch commands, fix audit_patch_status router for darwin"
```

---

## Task 9: Build darwin binary and upload

**Files:**
- `agent/nexplane-agent-darwin-arm64` (generated, not committed)

- [ ] **Step 1: Build darwin binary**

```bash
cd agent && GOOS=darwin GOARCH=arm64 go build -o nexplane-agent-darwin-arm64 . 2>&1
ls -lh nexplane-agent-darwin-arm64
```
Expected: binary ~18MB with recent timestamp

- [ ] **Step 2: SCP to EC2**

```bash
scp -i ~/.ssh/id_ed25519 agent/nexplane-agent-darwin-arm64 ec2-user@100.101.186.39:/tmp/nexplane-agent-darwin-arm64
```

- [ ] **Step 3: Upload to S3 and backend downloads dir**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec nexplane-backend-1 python3 -c \"
import boto3, shutil
s3 = boto3.client('s3')

# Copy binary into container first
import subprocess
subprocess.run(['docker', 'cp', '/tmp/nexplane-agent-darwin-arm64', 'nexplane-backend-1:/tmp/nexplane-agent-darwin-arm64'])
\"
docker cp /tmp/nexplane-agent-darwin-arm64 nexplane-backend-1:/tmp/nexplane-agent-darwin-arm64
docker exec nexplane-backend-1 python3 -c \"
import boto3, shutil
s3 = boto3.client('s3')
s3.upload_file('/tmp/nexplane-agent-darwin-arm64', 'nexplane-agent-downloads', 'nexplane-agent-darwin-arm64-0.3.0')
print('S3 upload done')
shutil.copy('/tmp/nexplane-agent-darwin-arm64', '/opt/nexplane-downloads/nexplane-agent-darwin-arm64-0.3.0')
print('local copy done')
\""
```

- [ ] **Step 4: Push code to EC2 and restart backend is NOT needed** — uvicorn auto-reloads on file changes. Only code changes require attention. Confirm EC2 is on latest commit:

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "cd /home/ec2-user/nexplane && git log --oneline -2"
```

- [ ] **Step 5: Run MAC smoke**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "
docker exec nexplane-backend-1 rm -f /tmp/smoke_done /tmp/smoke_mac.log
docker exec -d nexplane-backend-1 bash -c 'python3 /app/tests/smoke/run_on_ec2.py --phases MAC_AGENT_BOOTSTRAP,SANTA_SYNC --dedicated-host-id h-0d03a30df9b884c06 --ssh-key-path /tmp/nexplane-smoke-key.pem > /tmp/smoke_mac.log 2>&1 && touch /tmp/smoke_done || touch /tmp/smoke_done'
echo launched
"
```

Monitor:
```bash
ssh -i ~/.ssh/id_ed25519 -o ServerAliveInterval=30 ec2-user@100.101.186.39 "until docker exec nexplane-backend-1 test -f /tmp/smoke_done 2>/dev/null; do sleep 15; done; docker exec nexplane-backend-1 tail -100 /tmp/smoke_mac.log"
```

Expected: `[MAC_AGENT_BOOTSTRAP] PASSED`, `[SANTA_SYNC] PASSED`

- [ ] **Step 6: Release mac2.metal Dedicated Host after smoke passes**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec nexplane-backend-1 python3 -c \"
import boto3
ec2 = boto3.client('ec2', region_name='us-east-1')
# First terminate any running mac instance
reservations = ec2.describe_instances(Filters=[{'Name': 'host-id', 'Values': ['h-0d03a30df9b884c06']}, {'Name': 'instance-state-name', 'Values': ['running', 'stopped']}])['Reservations']
for r in reservations:
    for i in r['Instances']:
        print(f'Terminating {i[\\\"InstanceId\\\"]}')
        ec2.terminate_instances(InstanceIds=[i['InstanceId']])
import time; time.sleep(10)
ec2.release_hosts(HostIds=['h-0d03a30df9b884c06'])
print('Host released')
\""
```

- [ ] **Step 7: Commit any remaining changes and tag release**

```bash
git add -A
git diff --staged --stat
git commit -m "feat(agent): macOS feature parity — pf network policy, BSM LSM, lsof soak, patch, user lockout"
git push origin master
```

---

## Task 10: macOS-native network policy + LSM/audit commands

**New commands (no Linux equivalent — macOS-native naming):**
- `macos_pf_policy` — pfctl anchor rules for microsegmentation (enforce + rollback)
- `macos_audit_policy` — BSM audit_control configuration (enforce + rollback)
- `macos_tcc_audit` — read-only TCC database inventory (no rollback needed)

**Files to create:**
- `agent/commands/macos/macos_pf_policy_darwin.go` — pfctl anchor CRUD
- `agent/commands/macos/macos_audit_policy_darwin.go` — audit_control read/write
- `agent/commands/macos/macos_tcc_audit_darwin.go` — TCC DB query via sqlite3
- `agent/commands/macos/macos_pf_policy_other.go` — stubs for !darwin
- `agent/commands/macos/macos_audit_policy_other.go` — stubs for !darwin
- `agent/commands/macos/macos_tcc_audit_other.go` — stubs for !darwin

**Register in executor.go:** `macos_pf_policy`, `macos_audit_policy`, `macos_tcc_audit`

**Smoke:** Add smoke assertions for each in MAC_AGENT_BOOTSTRAP phase.

- [ ] Step 1: Implement `macos_pf_policy_darwin.go`
- [ ] Step 2: Implement `macos_audit_policy_darwin.go`
- [ ] Step 3: Implement `macos_tcc_audit_darwin.go`
- [ ] Step 4: Add stubs (_other.go files)
- [ ] Step 5: Register in executor.go
- [ ] Step 6: Add smoke assertions
- [ ] Step 7: Build 0.3.3 + upload to S3 from EC2
- [ ] Step 8: Run MAC smoke, verify pass

---

## Self-Review

**Spec coverage check (revised 2026-06-02):**
- `lock_local_user` darwin → Task 1 ✓
- `audit_mac_patch_status` + `apply_mac_patches` → Task 2 ✓
- ~~`ebpf_*` darwin~~ → REMOVED (eBPF is Linux-only)
- `authorized_keys_audit`, `sudoers_audit`, `suid_scan`, `ssl_cert_inspect`, `lynis_audit` on darwin → Task 7 ✓
- Register all in executor.go → Task 8 ✓
- Build darwin binary + MAC smoke → Task 9 ✓
- `macos_pf_policy`, `macos_audit_policy`, `macos_tcc_audit` → Task 10 ✓

**Type consistency:**
- All darwin functions match the Linux signatures: `func Foo(params map[string]any) (map[string]any, error)`

**Fix for `toFloat` duplication:**

Before Task 3, create `agent/commands/ebpf/ebpf_util.go`:

```go
package ebpf

func toFloat(v any) float64 {
	switch x := v.(type) {
	case float64:
		return x
	case int:
		return float64(x)
	case int64:
		return float64(x)
	}
	return 0
}
```

Then remove `toFloat` from `ebpf_soak_linux.go`, `ebpf_soak_darwin.go`, and `ebpf_soak_other.go`.

**`bsmAuditControl` / `bsmSnapshotDir` used in `ebpf_promote_darwin.go` but defined in `ebpf_lsm_policy_darwin.go`** — both are `darwin` build tag files in the same package. This is valid Go: package-level consts are visible across files with matching build tags.

**`pfAnchorFile` used in `ebpf_promote_darwin.go` but defined in `ebpf_network_policy_darwin.go`** — same package, same build tag. Valid.
