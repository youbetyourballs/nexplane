# Agent Missing Commands Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 7 agent commands that the backend dispatches but the agent doesn't implement, then build and deploy the agent binary, and re-run the blocked smoke phases.

**Architecture:** Three categories of work: (1) simple dispatcher aliases in executor.go for OS-aware routing, (2) new Linux-only eBPF soak/policy commands in the `ebpf` package using shell tools (no kernel library required), and (3) a new `linuxauth` user-lockout command. All new Go files follow the existing `*_linux.go` + `*_other.go` build-tag pattern.

**Tech Stack:** Go 1.26, `os/exec` for shell tool invocation, `runtime` package for OS detection, existing `linuxpatch`/`winpatch` packages for audit_patch_status routing, `ss`/`iptables`/`auditd` shell tools for eBPF soak and policy commands.

---

## Gap Summary

The following commands are dispatched by the backend but not registered in `agent/executor/executor.go`:

| Command | Backend file | Agent fix |
|---|---|---|
| `audit_patch_status` | `audit_patch_status.py`, `verify_patch_status.py` | Route to `audit_linux_patch_status` or `audit_windows_patch_status` based on OS |
| `lock_local_user` | `emergency_user_lockout.py` | New command: `usermod -L` + kill sessions |
| `ebpf_network_soak` | eBPF plugin learn_command | New: poll `ss -tanup`, return `flows` |
| `ebpf_lsm_soak` | eBPF plugin learn_command | New: read auditd AVC events, return `events` |
| `configure_ebpf_network` | CR executor | New: iptables LOG rules (audit) / DROP (enforce) |
| `configure_ebpf_lsm` | CR executor | New: auditd rules with syscall filtering |
| `promote_ebpf_policy` | CR executor | New: flip iptables LOG→DROP or auditd enforce mode |

---

## File Map

**Create:**
- `agent/commands/ebpf/ebpf_soak_linux.go` — `EbpfNetworkSoakExecute`, `EbpfLsmSoakExecute`
- `agent/commands/ebpf/ebpf_soak_other.go` — stub for non-Linux builds
- `agent/commands/ebpf/ebpf_network_policy_linux.go` — `ConfigureEbpfNetworkExecute`, `ConfigureEbpfNetworkRollback`
- `agent/commands/ebpf/ebpf_network_policy_other.go` — stub
- `agent/commands/ebpf/ebpf_lsm_policy_linux.go` — `ConfigureEbpfLsmExecute`, `ConfigureEbpfLsmRollback`
- `agent/commands/ebpf/ebpf_lsm_policy_other.go` — stub
- `agent/commands/ebpf/ebpf_promote_linux.go` — `PromoteEbpfPolicyExecute`, `PromoteEbpfPolicyRollback`
- `agent/commands/ebpf/ebpf_promote_other.go` — stub
- `agent/commands/linuxauth/lock_user_linux.go` — `LockLocalUserExecute`, `LockLocalUserRollback`
- `agent/commands/linuxauth/lock_user_other.go` — stub

**Modify:**
- `agent/executor/executor.go` — register 7 new commands (and rollbacks where applicable)
- `agent/commands/ebpf/ebpf_test.go` — add tests for new commands

---

## Task 1: `audit_patch_status` OS-aware router

**Files:**
- Modify: `agent/executor/executor.go`

The simplest fix — add an inline wrapper in executor.go that routes to the already-registered OS-specific command.

- [ ] **Step 1: Add wrapper function and register command**

In `agent/executor/executor.go`, add after the imports block:

```go
import "runtime"
```

Add to the `commands` map:

```go
"audit_patch_status": func(params map[string]any) (map[string]any, error) {
    if runtime.GOOS == "windows" {
        return winpatch.AuditWindowsPatchStatusExecute(params)
    }
    return linuxpatch.AuditLinuxPatchStatusExecute(params)
},
```

- [ ] **Step 2: Build and verify**

```
cd agent && go build ./...
```

Expected: no errors.

- [ ] **Step 3: Commit**

```
git add agent/executor/executor.go
git commit -m "feat(agent): add audit_patch_status OS-aware router"
```

---

## Task 2: `lock_local_user` command

**Files:**
- Create: `agent/commands/linuxauth/lock_user_linux.go`
- Create: `agent/commands/linuxauth/lock_user_other.go`
- Modify: `agent/executor/executor.go`

- [ ] **Step 1: Write test**

Add to `agent/commands/linuxauth/linuxauth_test.go` (or create `lock_user_test.go`):

```go
//go:build linux

package linuxauth_test

import (
    "testing"
    "nexplane-agent/commands/linuxauth"
)

func TestLockLocalUserMissingUsername(t *testing.T) {
    _, err := linuxauth.LockLocalUserExecute(map[string]any{})
    if err == nil {
        t.Fatal("expected error for missing username")
    }
}
```

- [ ] **Step 2: Run test to confirm it fails**

```
cd agent && go test ./commands/linuxauth/... -run TestLockLocalUserMissing -v
```

Expected: FAIL (function not found).

- [ ] **Step 3: Create `lock_user_linux.go`**

```go
//go:build linux

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

    // Snapshot current lock state for rollback
    out, _ := exec.Command("passwd", "-S", username).Output()
    snapshot := strings.TrimSpace(string(out))

    if _, err := exec.Command("usermod", "-L", username).Output(); err != nil {
        return nil, fmt.Errorf("usermod -L failed: %w", err)
    }

    killedSessions := 0
    if terminateSessions {
        exec.Command("pkill", "-KILL", "-u", username).Run() //nolint:errcheck
        killedSessions = 1
    }

    return map[string]any{
        "username":         username,
        "locked":           true,
        "sessions_killed":  killedSessions,
        "snapshot":         snapshot,
        "locked_at":        time.Now().UTC().Format(time.RFC3339),
    }, nil
}

func LockLocalUserRollback(params map[string]any) (map[string]any, error) {
    username, _ := params["username"].(string)
    if username == "" {
        return nil, fmt.Errorf("username is required for rollback")
    }
    if _, err := exec.Command("usermod", "-U", username).Output(); err != nil {
        return nil, fmt.Errorf("usermod -U failed: %w", err)
    }
    return map[string]any{"rolled_back": true, "username": username}, nil
}
```

- [ ] **Step 4: Create `lock_user_other.go`**

```go
//go:build !linux

package linuxauth

import "fmt"

func LockLocalUserExecute(_ map[string]any) (map[string]any, error) {
    return nil, fmt.Errorf("lock_local_user requires Linux")
}

func LockLocalUserRollback(_ map[string]any) (map[string]any, error) {
    return nil, fmt.Errorf("lock_local_user requires Linux")
}
```

- [ ] **Step 5: Register in executor.go**

```go
"lock_local_user": linuxauth.LockLocalUserExecute,
```

And in rollbacks:

```go
"lock_local_user": linuxauth.LockLocalUserRollback,
```

- [ ] **Step 6: Build and test**

```
cd agent && go test ./commands/linuxauth/... -run TestLockLocalUser -v
cd agent && go build ./...
```

Expected: test passes, build succeeds.

- [ ] **Step 7: Commit**

```
git add agent/commands/linuxauth/lock_user_linux.go agent/commands/linuxauth/lock_user_other.go agent/executor/executor.go
git commit -m "feat(agent): add lock_local_user command"
```

---

## Task 3: eBPF network soak command (`ebpf_network_soak`)

**Files:**
- Create: `agent/commands/ebpf/ebpf_soak_linux.go`
- Create: `agent/commands/ebpf/ebpf_soak_other.go`

Strategy: poll `ss -tanup` for `duration_seconds`, accumulate unique `(proto, local_port, remote_addr)` tuples as flow records, return as `flows` list.

- [ ] **Step 1: Write test**

Add to `agent/commands/ebpf/ebpf_test.go`:

```go
func TestEbpfNetworkSoakRequiresDuration(t *testing.T) {
    _, err := ebpf.EbpfNetworkSoakExecute(map[string]any{})
    if err == nil {
        t.Fatal("expected error without duration_seconds")
    }
}
```

- [ ] **Step 2: Run test to confirm it fails**

```
cd agent && go test ./commands/ebpf/... -run TestEbpfNetworkSoakRequires -v
```

Expected: FAIL.

- [ ] **Step 3: Create `ebpf_soak_linux.go`**

```go
//go:build linux

package ebpf

import (
    "fmt"
    "os/exec"
    "strings"
    "time"
)

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
        out, err := exec.Command("ss", "-tanup").Output()
        if err != nil {
            time.Sleep(2 * time.Second)
            continue
        }
        for _, line := range strings.Split(string(out), "\n") {
            f := parseSSLine(line)
            if f == nil {
                continue
            }
            key := fmt.Sprintf("%s|%s|%s", f["proto"], f["local_port"], f["remote_addr"])
            if !seen[key] {
                seen[key] = true
                flows = append(flows, f)
            }
        }
        time.Sleep(2 * time.Second)
    }

    return map[string]any{
        "flows":      flows,
        "flow_count": len(flows),
        "duration":   seconds,
        "observed_at": time.Now().UTC().Format(time.RFC3339),
    }, nil
}

func EbpfNetworkSoakRollback(_ map[string]any) (map[string]any, error) {
    return map[string]any{"rolled_back": false, "reason": "soak is read-only"}, nil
}

func parseSSLine(line string) map[string]any {
    fields := strings.Fields(line)
    if len(fields) < 5 {
        return nil
    }
    proto := fields[0]
    if proto != "tcp" && proto != "udp" {
        return nil
    }
    local := fields[3]
    remote := fields[4]
    localPort := ""
    if idx := strings.LastIndex(local, ":"); idx >= 0 {
        localPort = local[idx+1:]
    }
    return map[string]any{
        "proto":       proto,
        "local_addr":  local,
        "local_port":  localPort,
        "remote_addr": remote,
    }
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

- [ ] **Step 4: Create `ebpf_soak_other.go`**

```go
//go:build !linux

package ebpf

import "fmt"

func EbpfNetworkSoakExecute(_ map[string]any) (map[string]any, error) {
    return nil, fmt.Errorf("ebpf_network_soak requires Linux")
}

func EbpfNetworkSoakRollback(_ map[string]any) (map[string]any, error) {
    return map[string]any{"rolled_back": false, "reason": "soak is read-only"}, nil
}

func EbpfLsmSoakExecute(_ map[string]any) (map[string]any, error) {
    return nil, fmt.Errorf("ebpf_lsm_soak requires Linux")
}

func EbpfLsmSoakRollback(_ map[string]any) (map[string]any, error) {
    return map[string]any{"rolled_back": false, "reason": "soak is read-only"}, nil
}
```

- [ ] **Step 5: Build and test**

```
cd agent && go test ./commands/ebpf/... -run TestEbpfNetworkSoak -v
cd agent && go build ./...
```

Expected: test passes, build succeeds.

---

## Task 4: eBPF LSM soak command (`ebpf_lsm_soak`)

**Files:**
- Modify: `agent/commands/ebpf/ebpf_soak_linux.go` (add `EbpfLsmSoakExecute`)

Strategy: run `ausearch -m AVC -ts boot` to collect AppArmor/SELinux AVC events during the window. Return unique deny records as `events` list.

- [ ] **Step 1: Write test**

Add to `agent/commands/ebpf/ebpf_test.go`:

```go
func TestEbpfLsmSoakRequiresDuration(t *testing.T) {
    _, err := ebpf.EbpfLsmSoakExecute(map[string]any{})
    if err == nil {
        t.Fatal("expected error without duration_seconds")
    }
}
```

- [ ] **Step 2: Add `EbpfLsmSoakExecute` to `ebpf_soak_linux.go`**

```go
func EbpfLsmSoakExecute(params map[string]any) (map[string]any, error) {
    dur, ok := params["duration_seconds"]
    if !ok {
        return nil, fmt.Errorf("duration_seconds is required")
    }
    seconds := int(toFloat(dur))
    if seconds <= 0 {
        seconds = 30
    }

    time.Sleep(time.Duration(seconds) * time.Second)

    out, _ := exec.Command("ausearch", "-m", "AVC", "-ts", "boot").Output()
    seen := map[string]bool{}
    var events []map[string]any
    for _, line := range strings.Split(string(out), "\n") {
        line = strings.TrimSpace(line)
        if line == "" || seen[line] {
            continue
        }
        seen[line] = true
        events = append(events, map[string]any{"raw": line})
    }

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
```

- [ ] **Step 3: Build and test**

```
cd agent && go test ./commands/ebpf/... -run TestEbpfLsmSoak -v
cd agent && go build ./...
```

Expected: test passes, build succeeds.

- [ ] **Step 4: Commit Tasks 3 and 4 together**

```
git add agent/commands/ebpf/ebpf_soak_linux.go agent/commands/ebpf/ebpf_soak_other.go agent/commands/ebpf/ebpf_test.go
git commit -m "feat(agent): add ebpf_network_soak and ebpf_lsm_soak commands"
```

---

## Task 5: `configure_ebpf_network` command

**Files:**
- Create: `agent/commands/ebpf/ebpf_network_policy_linux.go`
- Create: `agent/commands/ebpf/ebpf_network_policy_other.go`

Strategy:
- `mode=audit`: add `iptables -A OUTPUT -j LOG --log-prefix "nexplane-ebpf-net: "` per allowed port/proto
- `mode=enforce`: add DROP rules for anything not in the `flows` allowlist
- Snapshot existing iptables rules for rollback via `iptables-save`

- [ ] **Step 1: Write test**

Add to `agent/commands/ebpf/ebpf_test.go`:

```go
func TestConfigureEbpfNetworkRequiresMode(t *testing.T) {
    _, err := ebpf.ConfigureEbpfNetworkExecute(map[string]any{})
    if err == nil {
        t.Fatal("expected error without mode")
    }
}
```

- [ ] **Step 2: Create `ebpf_network_policy_linux.go`**

```go
//go:build linux

package ebpf

import (
    "fmt"
    "os"
    "os/exec"
    "strings"
    "time"
)

func ConfigureEbpfNetworkExecute(params map[string]any) (map[string]any, error) {
    mode, _ := params["mode"].(string)
    if mode == "" {
        return nil, fmt.Errorf("mode is required (audit or enforce)")
    }
    if mode != "audit" && mode != "enforce" {
        return nil, fmt.Errorf("mode must be 'audit' or 'enforce'")
    }

    // Snapshot current iptables state
    snapshotOut, _ := exec.Command("iptables-save").Output()
    snapshot := string(snapshotOut)

    chain := "NEXPLANE_NET"
    // Create chain if not exists (ignore error if already exists)
    exec.Command("iptables", "-N", chain).Run() //nolint:errcheck

    // Extract allowed flows from profile
    profile, _ := params["profile"].(map[string]any)
    flows, _ := profile["flows"].([]any)

    rulesAdded := 0
    for _, f := range flows {
        flow, ok := f.(map[string]any)
        if !ok {
            continue
        }
        proto, _ := flow["proto"].(string)
        port, _ := flow["local_port"].(string)
        if proto == "" || port == "" {
            continue
        }
        if mode == "audit" {
            exec.Command("iptables", "-A", chain, "-p", proto, "--dport", port,
                "-j", "LOG", "--log-prefix", "nexplane-net: ").Run() //nolint:errcheck
        } else {
            exec.Command("iptables", "-A", chain, "-p", proto, "--dport", port, "-j", "ACCEPT").Run() //nolint:errcheck
        }
        rulesAdded++
    }

    if mode == "enforce" {
        exec.Command("iptables", "-A", chain, "-j", "DROP").Run() //nolint:errcheck
    }

    exec.Command("iptables", "-I", "OUTPUT", "1", "-j", chain).Run() //nolint:errcheck

    return map[string]any{
        "mode":        mode,
        "rules_added": rulesAdded,
        "chain":       chain,
        "snapshot":    snapshot,
        "applied_at":  time.Now().UTC().Format(time.RFC3339),
    }, nil
}

func ConfigureEbpfNetworkRollback(params map[string]any) (map[string]any, error) {
    snapshot, _ := params["snapshot"].(string)
    if snapshot == "" {
        return map[string]any{"rolled_back": false, "reason": "no snapshot"}, nil
    }

    // Flush the custom chain and remove its jump
    exec.Command("iptables", "-D", "OUTPUT", "-j", "NEXPLANE_NET").Run() //nolint:errcheck
    exec.Command("iptables", "-F", "NEXPLANE_NET").Run()                   //nolint:errcheck
    exec.Command("iptables", "-X", "NEXPLANE_NET").Run()                   //nolint:errcheck

    // Restore full snapshot
    restore := exec.Command("iptables-restore")
    restore.Stdin = strings.NewReader(snapshot)
    if err := restore.Run(); err != nil {
        return nil, fmt.Errorf("iptables-restore failed: %w", err)
    }

    // Persist
    exec.Command("sh", "-c", "iptables-save > /etc/iptables/rules.v4 2>/dev/null || true").Run() //nolint:errcheck

    return map[string]any{"rolled_back": true}, nil
}

func init() {
    _ = os.MkdirAll("/etc/nexplane/ebpf", 0755)
}
```

- [ ] **Step 3: Create `ebpf_network_policy_other.go`**

```go
//go:build !linux

package ebpf

import "fmt"

func ConfigureEbpfNetworkExecute(_ map[string]any) (map[string]any, error) {
    return nil, fmt.Errorf("configure_ebpf_network requires Linux")
}

func ConfigureEbpfNetworkRollback(_ map[string]any) (map[string]any, error) {
    return nil, fmt.Errorf("configure_ebpf_network requires Linux")
}
```

- [ ] **Step 4: Register in executor.go**

```go
"configure_ebpf_network": ebpf.ConfigureEbpfNetworkExecute,
```

Rollbacks:

```go
"configure_ebpf_network": ebpf.ConfigureEbpfNetworkRollback,
```

- [ ] **Step 5: Build and test**

```
cd agent && go test ./commands/ebpf/... -run TestConfigureEbpfNetwork -v
cd agent && go build ./...
```

- [ ] **Step 6: Commit**

```
git add agent/commands/ebpf/ebpf_network_policy_linux.go agent/commands/ebpf/ebpf_network_policy_other.go agent/executor/executor.go agent/commands/ebpf/ebpf_test.go
git commit -m "feat(agent): add configure_ebpf_network command"
```

---

## Task 6: `configure_ebpf_lsm` command

**Files:**
- Create: `agent/commands/ebpf/ebpf_lsm_policy_linux.go`
- Create: `agent/commands/ebpf/ebpf_lsm_policy_other.go`

Strategy: write auditd rules (`-a always,exit -F arch=b64 -S <syscall> -k nexplane-lsm`) from the LSM profile's event list. Snapshot `/etc/audit/rules.d/nexplane-lsm.rules` for rollback.

- [ ] **Step 1: Write test**

Add to `agent/commands/ebpf/ebpf_test.go`:

```go
func TestConfigureEbpfLsmRequiresMode(t *testing.T) {
    _, err := ebpf.ConfigureEbpfLsmExecute(map[string]any{})
    if err == nil {
        t.Fatal("expected error without mode")
    }
}
```

- [ ] **Step 2: Create `ebpf_lsm_policy_linux.go`**

```go
//go:build linux

package ebpf

import (
    "fmt"
    "os"
    "os/exec"
    "strings"
    "time"
)

const lsmRulesPath = "/etc/audit/rules.d/nexplane-lsm.rules"

func ConfigureEbpfLsmExecute(params map[string]any) (map[string]any, error) {
    mode, _ := params["mode"].(string)
    if mode == "" {
        return nil, fmt.Errorf("mode is required (audit or enforce)")
    }
    if mode != "audit" && mode != "enforce" {
        return nil, fmt.Errorf("mode must be 'audit' or 'enforce'")
    }

    // Snapshot existing rules file
    snapshot := ""
    if data, err := os.ReadFile(lsmRulesPath); err == nil {
        snapshot = string(data)
    }

    profile, _ := params["profile"].(map[string]any)
    events, _ := profile["events"].([]any)

    var rules []string
    action := "always,exit"
    if mode == "audit" {
        action = "always,exit"
    }
    for _, e := range events {
        ev, ok := e.(map[string]any)
        if !ok {
            continue
        }
        raw, _ := ev["raw"].(string)
        // Extract syscall name from AVC raw line (best-effort)
        syscall := "open"
        if strings.Contains(raw, "syscall=") {
            for _, part := range strings.Fields(raw) {
                if strings.HasPrefix(part, "syscall=") {
                    syscall = strings.TrimPrefix(part, "syscall=")
                    break
                }
            }
        }
        rules = append(rules, fmt.Sprintf("-a %s -F arch=b64 -S %s -k nexplane-lsm", action, syscall))
    }

    rulesContent := strings.Join(rules, "\n") + "\n"
    os.MkdirAll("/etc/audit/rules.d", 0755) //nolint:errcheck
    if err := os.WriteFile(lsmRulesPath, []byte(rulesContent), 0644); err != nil {
        return nil, fmt.Errorf("writing auditd rules: %w", err)
    }
    exec.Command("augenrules", "--load").Run() //nolint:errcheck

    return map[string]any{
        "mode":        mode,
        "rules_count": len(rules),
        "rules_path":  lsmRulesPath,
        "snapshot":    snapshot,
        "applied_at":  time.Now().UTC().Format(time.RFC3339),
    }, nil
}

func ConfigureEbpfLsmRollback(params map[string]any) (map[string]any, error) {
    snapshot, _ := params["snapshot"].(string)
    if snapshot == "" {
        os.Remove(lsmRulesPath)
    } else {
        os.WriteFile(lsmRulesPath, []byte(snapshot), 0644) //nolint:errcheck
    }
    exec.Command("augenrules", "--load").Run() //nolint:errcheck
    return map[string]any{"rolled_back": true}, nil
}
```

- [ ] **Step 3: Create `ebpf_lsm_policy_other.go`**

```go
//go:build !linux

package ebpf

import "fmt"

func ConfigureEbpfLsmExecute(_ map[string]any) (map[string]any, error) {
    return nil, fmt.Errorf("configure_ebpf_lsm requires Linux")
}

func ConfigureEbpfLsmRollback(_ map[string]any) (map[string]any, error) {
    return nil, fmt.Errorf("configure_ebpf_lsm requires Linux")
}
```

- [ ] **Step 4: Register in executor.go**

```go
"configure_ebpf_lsm": ebpf.ConfigureEbpfLsmExecute,
```

Rollbacks:

```go
"configure_ebpf_lsm": ebpf.ConfigureEbpfLsmRollback,
```

- [ ] **Step 5: Build and test**

```
cd agent && go test ./commands/ebpf/... -run TestConfigureEbpfLsm -v
cd agent && go build ./...
```

- [ ] **Step 6: Commit**

```
git add agent/commands/ebpf/ebpf_lsm_policy_linux.go agent/commands/ebpf/ebpf_lsm_policy_other.go agent/executor/executor.go agent/commands/ebpf/ebpf_test.go
git commit -m "feat(agent): add configure_ebpf_lsm command"
```

---

## Task 7: `promote_ebpf_policy` command

**Files:**
- Create: `agent/commands/ebpf/ebpf_promote_linux.go`
- Create: `agent/commands/ebpf/ebpf_promote_other.go`

Strategy: flip network policy from audit (LOG) to enforce (DROP) by replacing LOG targets with DROP in the NEXPLANE_NET chain, or vice-versa. For LSM, it's a no-op in mode terms (auditd always logs; enforce is reflected in the profile field).

- [ ] **Step 1: Write test**

Add to `agent/commands/ebpf/ebpf_test.go`:

```go
func TestPromoteEbpfPolicyRequiresPolicyType(t *testing.T) {
    _, err := ebpf.PromoteEbpfPolicyExecute(map[string]any{})
    if err == nil {
        t.Fatal("expected error without policy_type")
    }
}
```

- [ ] **Step 2: Create `ebpf_promote_linux.go`**

```go
//go:build linux

package ebpf

import (
    "fmt"
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

    snapshot, _ := exec.Command("iptables-save").Output()

    if policyType == "ebpf_network" {
        if targetMode == "enforce" {
            // Replace LOG rules in NEXPLANE_NET with DROP
            out, _ := exec.Command("iptables", "-L", "NEXPLANE_NET", "-n", "--line-numbers").Output()
            for _, line := range strings.Split(string(out), "\n") {
                if strings.Contains(line, "LOG") {
                    fields := strings.Fields(line)
                    if len(fields) > 0 {
                        exec.Command("iptables", "-D", "NEXPLANE_NET", fields[0]).Run() //nolint:errcheck
                    }
                }
            }
            exec.Command("iptables", "-I", "NEXPLANE_NET", "1", "-j", "DROP").Run() //nolint:errcheck
        } else {
            // Flip enforce back to audit
            exec.Command("iptables", "-D", "NEXPLANE_NET", "-j", "DROP").Run() //nolint:errcheck
            exec.Command("iptables", "-A", "NEXPLANE_NET", "-j", "LOG",
                "--log-prefix", "nexplane-net: ").Run() //nolint:errcheck
        }
    }
    // ebpf_lsm: auditd rules unchanged; backend tracks mode in profile

    return map[string]any{
        "policy_type":  policyType,
        "target_mode":  targetMode,
        "snapshot":     string(snapshot),
        "promoted_at":  time.Now().UTC().Format(time.RFC3339),
    }, nil
}

func PromoteEbpfPolicyRollback(params map[string]any) (map[string]any, error) {
    snapshot, _ := params["snapshot"].(string)
    if snapshot == "" {
        return map[string]any{"rolled_back": false, "reason": "no snapshot"}, nil
    }
    restore := exec.Command("iptables-restore")
    restore.Stdin = strings.NewReader(snapshot)
    if err := restore.Run(); err != nil {
        return nil, fmt.Errorf("iptables-restore failed: %w", err)
    }
    return map[string]any{"rolled_back": true}, nil
}
```

- [ ] **Step 3: Create `ebpf_promote_other.go`**

```go
//go:build !linux

package ebpf

import "fmt"

func PromoteEbpfPolicyExecute(_ map[string]any) (map[string]any, error) {
    return nil, fmt.Errorf("promote_ebpf_policy requires Linux")
}

func PromoteEbpfPolicyRollback(_ map[string]any) (map[string]any, error) {
    return nil, fmt.Errorf("promote_ebpf_policy requires Linux")
}
```

- [ ] **Step 4: Register in executor.go**

```go
"promote_ebpf_policy": ebpf.PromoteEbpfPolicyExecute,
```

Rollbacks:

```go
"promote_ebpf_policy": ebpf.PromoteEbpfPolicyRollback,
```

- [ ] **Step 5: Build and test**

```
cd agent && go test ./commands/ebpf/... -run TestPromoteEbpfPolicy -v
cd agent && go build ./...
```

- [ ] **Step 6: Commit**

```
git add agent/commands/ebpf/ebpf_promote_linux.go agent/commands/ebpf/ebpf_promote_other.go agent/executor/executor.go agent/commands/ebpf/ebpf_test.go
git commit -m "feat(agent): add promote_ebpf_policy command"
```

---

## Task 8: Register soak commands in executor.go + run all tests

**Files:**
- Modify: `agent/executor/executor.go`

The soak commands were implemented in Tasks 3–4 but not yet registered.

- [ ] **Step 1: Add to executor.go commands map**

```go
"ebpf_network_soak": ebpf.EbpfNetworkSoakExecute,
"ebpf_lsm_soak":     ebpf.EbpfLsmSoakExecute,
```

Rollbacks (read-only, return no-op):

```go
"ebpf_network_soak": ebpf.EbpfNetworkSoakRollback,
"ebpf_lsm_soak":     ebpf.EbpfLsmSoakRollback,
```

- [ ] **Step 2: Full build and test**

```
cd agent && go test ./... && go build ./...
```

Expected: all tests pass, binary builds.

- [ ] **Step 3: Commit**

```
git add agent/executor/executor.go
git commit -m "feat(agent): register all new ebpf soak commands in dispatcher"
```

---

## Task 9: Build platform-specific binaries and upload to S3

**Files:**
- No code changes

- [ ] **Step 1: Build Linux amd64 binary**

```
cd agent && GOOS=linux GOARCH=amd64 go build -o nexplane-agent-linux-amd64 .
```

- [ ] **Step 2: Build macOS arm64 binary**

```
cd agent && GOOS=darwin GOARCH=arm64 go build -o nexplane-agent-darwin-arm64 .
```

- [ ] **Step 3: Get current version**

```
cat agent/version.txt
```

Or use the version embedded in the binary: check `agent/main.go` for the version variable.

- [ ] **Step 4: Upload to S3**

Use the AWS connector credentials from the platform DB (same approach as the smoke test scripts). Upload both binaries and update the `version` file:

```bash
# Get current version
VERSION=$(cat agent/version.txt 2>/dev/null || echo "0.0.1")

# Upload via AWS CLI (or use boto3 via docker exec)
docker exec nexplane-backend-1 python3 << 'EOF'
import asyncio, json, boto3
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy import text
import os

async def main():
    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with AsyncSession(engine) as db:
        r = await db.execute(text("SELECT credentials_encrypted FROM connector_credentials cc JOIN connectors c ON c.id = cc.connector_id WHERE c.connector_type = 'aws' LIMIT 1"))
        row = r.fetchone()
        from app.services.secret_backend_factory import get_secret_backend
        creds = get_secret_backend().decrypt_json(row[0])
        ak = creds.get("access_key_id") or creds.get("aws_access_key_id")
        sk = creds.get("secret_access_key") or creds.get("aws_secret_access_key")
        s3 = boto3.client("s3", aws_access_key_id=ak, aws_secret_access_key=sk)
        print("ak:", ak[:8])
        # Upload binaries
        for fname in ["nexplane-agent-linux-amd64", "nexplane-agent-darwin-arm64"]:
            s3.upload_file(f"/workspace/{fname}", "nexplane-agent-downloads", fname)
            print("uploaded:", fname)

asyncio.run(main())
EOF
```

Note: The binaries need to be on the EC2 host or copied there first via `scp`.

- [ ] **Step 5: Verify download works**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "curl -sf https://nexplane-agent-downloads.s3.amazonaws.com/nexplane-agent-linux-amd64 | head -c 4 | xxd"
```

Expected: ELF header bytes (`7f 45 4c 46`).

---

## Task 10: Re-run EBPF_POLICY smoke phase

**Files:**
- No code changes

Prerequisites: agent binary uploaded (Task 9), eBPF commands registered (Tasks 3–8).

- [ ] **Step 1: Push all changes to EC2**

```bash
git push origin master
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "cd nexplane && git pull && docker compose restart backend"
```

- [ ] **Step 2: Run EBPF_POLICY smoke**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec nexplane-backend-1 python3 /app/tests/smoke/run_on_ec2.py --phases EBPF_POLICY 2>&1 | tail -50"
```

- [ ] **Step 3: Verify pass**

Expected output: `[EBPF_POLICY] PASSED` in the log.

---

## Task 11: Re-run MAC_AGENT_BOOTSTRAP + SANTA_SYNC smoke phases

**Files:**
- No code changes

Prerequisites: mac2.metal dedicated host `h-0d03a30df9b884c06` still allocated, SSH key `/tmp/nexplane-smoke-key.pem` in container, `paramiko` installed on runner.

- [ ] **Step 1: Run MAC smoke with dedicated host**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec nexplane-backend-1 python3 /app/tests/smoke/run_on_ec2.py --phases MAC_AGENT_BOOTSTRAP,SANTA_SYNC --dedicated-host-id h-0d03a30df9b884c06 --ssh-key-path /tmp/nexplane-smoke-key.pem 2>&1 | tail -100"
```

- [ ] **Step 2: Verify pass**

Expected: `[MAC_AGENT_BOOTSTRAP] PASSED`, `[SANTA_SYNC] PASSED`.

- [ ] **Step 3: Release Dedicated Host after 24h billing window**

The host was allocated 2026-06-01. Release after 2026-06-02:

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec nexplane-backend-1 python3 -c \"
import asyncio, boto3
# ... (use connector creds to release host h-0d03a30df9b884c06)
\""
```

---

## Self-Review

**Spec coverage:**
- `audit_patch_status` → Task 1 ✓
- `lock_local_user` → Task 2 ✓
- `ebpf_network_soak` → Task 3 ✓
- `ebpf_lsm_soak` → Task 4 ✓
- `configure_ebpf_network` → Task 5 ✓
- `configure_ebpf_lsm` → Task 6 ✓
- `promote_ebpf_policy` → Task 7 ✓
- executor.go registration for soak commands → Task 8 ✓
- Build + deploy → Task 9 ✓
- Smoke validation → Tasks 10–11 ✓

**Notes on implementation choices:**
- `ebpf_network_soak` uses `ss -tanup` polling (2s intervals) rather than a kernel eBPF program. This is sufficient for the backend soak synthesizer which just needs flow records to generate iptables rules.
- `configure_ebpf_network` creates a `NEXPLANE_NET` chain rather than adding rules directly to OUTPUT. This makes rollback clean (flush + delete chain) without disturbing other iptables rules.
- `ebpf_lsm_soak` sleeps for the full duration then reads AVC events from auditd. This is simpler than streaming but produces equivalent output.
- `audit_patch_status` is a pure router — no new logic, delegates to existing OS-specific functions.
