# Agent Parity SP7: Remaining Darwin Stubs + Build Tag Fixes Design

**Session:** 2026-06-09  
**Status:** Approved

---

## Scope

Four additional darwin stubs with real equivalents, one virtualize stub (no equivalent), and all `_other.go` build tag fixes to ensure darwin implementations aren't masked.

### Darwin Stubs to Implement

| File | Status |
|------|--------|
| `configsyslog_darwin.go` | Implement — `/etc/syslog.conf` forwarding |
| `crossplatform_darwin.go` | Implement — TLS (openssl), DNS (networksetup), inventory (system_profiler + brew/port) |
| `estimatesize_darwin.go` | Implement — `du` + `df` (same CLI as Linux) |
| `fleet_darwin.go` | Implement — `launchctl` restart, push (pure Go, same as Linux), health check (HTTP) |
| `virtualize_darwin.go` | No standard equivalent — document clearly and keep returning error |

### Build Tag Fixes

All `_other.go` / `_linux.go` files whose build tags exclude darwin but now have a real darwin implementation need `&& !darwin` added.

### Windows Gaps

| File | Action |
|------|--------|
| `credrotation/db_windows.go` | Implement `updateDBUserPassword` — psql/mysql CLIs exist on Windows |
| `compliance_windows.go` | Deferred — Windows CIS audit requires Group Policy/secedit/registry scripting; existing "not supported" error is acceptable for MVP |

---

## configsyslog_darwin.go

macOS ships `syslogd` which reads `/etc/syslog.conf`. The forwarding syntax is identical to traditional BSD syslog.

```go
//go:build darwin

package configsyslog

import (
    "fmt"
    "os"
    "strings"
    "os/exec"
    "time"
)

const syslogConf = "/etc/syslog.conf"

func executeOS(params map[string]any) (map[string]any, error) {
    host, _ := params["destination_host"].(string)
    port := params["destination_port"]
    proto, _ := params["protocol"].(string)
    facility, _ := params["facility"].(string)
    if host == "" {
        return nil, fmt.Errorf("destination_host is required")
    }
    if proto == "" {
        proto = "udp"
    }
    if facility == "" {
        facility = "*.*"
    }
    portInt := toInt(port)
    if portInt == 0 {
        portInt = 514
    }
    
    existing, _ := os.ReadFile(syslogConf)
    snapshot := string(existing)
    
    var forwardLine string
    if proto == "tcp" {
        forwardLine = fmt.Sprintf("%s\t@@%s:%d", facility, host, portInt)
    } else {
        forwardLine = fmt.Sprintf("%s\t@%s:%d", facility, host, portInt)
    }
    
    // Remove existing nexplane block if present, add new one
    content := removeBlock(snapshot, nexplaneBegin, nexplaneEnd)
    block := fmt.Sprintf("\n%s\n%s\n%s\n", nexplaneBegin, forwardLine, nexplaneEnd)
    content += block
    
    if err := os.WriteFile(syslogConf, []byte(content), 0644); err != nil {
        return nil, fmt.Errorf("writing syslog.conf: %w", err)
    }
    // Reload syslogd
    exec.Command("launchctl", "kickstart", "-k", "system/com.apple.syslogd").Run()
    
    return map[string]any{
        "daemon":      "syslogd",
        "config_path": syslogConf,
        "snapshot":    snapshot,
        "applied_at":  time.Now().UTC().Format(time.RFC3339),
    }, nil
}

func rollbackOS(params map[string]any) (map[string]any, error) {
    snapshot, ok := params["snapshot"].(string)
    if !ok {
        return nil, fmt.Errorf("snapshot is required for rollback")
    }
    if err := os.WriteFile(syslogConf, []byte(snapshot), 0644); err != nil {
        return nil, fmt.Errorf("restoring syslog.conf: %w", err)
    }
    exec.Command("launchctl", "kickstart", "-k", "system/com.apple.syslogd").Run()
    return map[string]any{"rolled_back": true}, nil
}

// removeBlock strips nexplane-managed-begin…end block from content.
func removeBlock(content, begin, end string) string {
    lines := strings.Split(content, "\n")
    var out []string
    inBlock := false
    for _, l := range lines {
        if strings.TrimSpace(l) == begin {
            inBlock = true
            continue
        }
        if strings.TrimSpace(l) == end {
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

---

## crossplatform_darwin.go

**TLS (`tlsExecuteOS`):** `openssl` is available on macOS (system or Homebrew). Cert paths for known services can follow `/etc/nexplane/tls/<service>/`. Service restart uses `launchctl` instead of `systemctl`.

```go
func tlsExecuteOS(params map[string]any) (map[string]any, error) {
    // Near-identical to Linux version. Differences:
    // 1. No certbot --webroot (use standalone or DNS challenge instead)
    // 2. Service restart: launchctl kickstart -k system/<service>
    // 3. Cert storage: /etc/nexplane/tls/<service>/ (same)
    // openssl available at /usr/bin/openssl (system) or /opt/homebrew/bin/openssl
}

func tlsRollbackOS(params map[string]any) (map[string]any, error) {
    // Restore snapshot cert/key files, restart service
}
```

**DNS (`dnsExecuteOS`):** Use `networksetup -setdnsservers <service> <servers...>` to set DNS resolvers per interface. Snapshot via `networksetup -getdnsservers <service>`.

```go
func dnsExecuteOS(params map[string]any) (map[string]any, error) {
    resolversRaw, _ := params["resolvers"].([]any)
    resolvers := toStringSlice(resolversRaw)
    
    // Get active network service
    out, _ := exec.Command("networksetup", "-listallnetworkservices").Output()
    // Use first active service (Wi-Fi or Ethernet)
    service := detectActiveNetworkService()
    
    snapshot, _ := exec.Command("networksetup", "-getdnsservers", service).Output()
    
    args := append([]string{"-setdnsservers", service}, resolvers...)
    if out2, err := exec.Command("networksetup", args...).CombinedOutput(); err != nil {
        return nil, fmt.Errorf("networksetup: %s: %w", out2, err)
    }
    return map[string]any{
        "service": service, "resolvers": resolvers,
        "snapshot": strings.TrimSpace(string(snapshot)),
    }, nil
}
```

**Inventory (`inventoryExecuteOS`):**
```go
func inventoryExecuteOS(_ map[string]any) (map[string]any, error) {
    packages := []map[string]any{}
    // Homebrew
    if out, err := exec.Command("brew", "list", "--versions").Output(); err == nil {
        for _, line := range strings.Split(string(out), "\n") {
            parts := strings.Fields(line)
            if len(parts) >= 2 {
                packages = append(packages, map[string]any{
                    "name": parts[0], "version": parts[1], "source": "brew",
                })
            }
        }
    }
    // MacPorts
    if out, err := exec.Command("port", "installed").Output(); err == nil {
        // Parse port output: "  name @version_revision (active)"
    }
    // System packages via pkgutil
    if out, err := exec.Command("pkgutil", "--pkgs").Output(); err == nil {
        // Each line is a bundle ID; get version via pkgutil --pkg-info
    }
    return map[string]any{"packages": packages, "collected_at": nowISO()}, nil
}
```

---

## estimatesize_darwin.go

`du` and `df` behave identically on macOS (BSD variant). The only difference from Linux is `-B` flag for block size isn't supported; use `-k` instead.

```go
func executeOS(params map[string]any) (map[string]any, error) {
    path, _ := params["path"].(string)
    if path == "" {
        path = "/"
    }
    // du -sk <path> for size in KB
    out, err := exec.Command("du", "-sk", path).Output()
    if err != nil {
        return nil, fmt.Errorf("du -sk %s: %w", path, err)
    }
    parts := strings.Fields(string(out))
    sizeKB, _ := strconv.ParseInt(parts[0], 10, 64)
    
    // df -k <path> for available space
    dfOut, _ := exec.Command("df", "-k", path).Output()
    
    return map[string]any{
        "path":         path,
        "size_bytes":   sizeKB * 1024,
        "df_output":    strings.TrimSpace(string(dfOut)),
        "collected_at": nowISO(),
    }, nil
}
```

---

## fleet_darwin.go

```go
func restartServiceOS(ctx context.Context, serviceName string) map[string]any {
    // Try launchd system service first
    plist := fmt.Sprintf("system/com.%s", serviceName) // best-effort label
    out, err := exec.CommandContext(ctx, "launchctl", "kickstart", "-k", plist).CombinedOutput()
    if err != nil {
        // Try brew services
        out2, err2 := exec.CommandContext(ctx, "brew", "services", "restart", serviceName).CombinedOutput()
        if err2 != nil {
            return map[string]any{"running": false, "error": fmt.Sprintf("launchctl: %s; brew: %s", out, out2)}
        }
        return map[string]any{"running": true, "output": string(out2)}
    }
    return map[string]any{"running": true, "output": string(out)}
}

func pushConfigFileOS(params map[string]any) map[string]any {
    // Identical to Linux version — pure Go file write, no OS calls
    // Copy implementation from fleet_linux.go
}

func healthCheckOS(ctx context.Context, endpoints []string) map[string]any {
    // Identical to Linux version — pure HTTP client calls
    // Copy implementation from fleet_linux.go
}

func runPostCommand(ctx context.Context, command string) map[string]any {
    // Identical to Linux version — exec.CommandContext via /bin/sh -c
}
```

---

## virtualize_darwin.go — No Equivalent

macOS has no standard CLI for virtualization that parallels Linux's `virsh`/`qemu-system-*`. Hypervisors (Parallels, VMware Fusion, VirtualBox, Apple Virtualization.framework) each have their own CLI without a unified interface. This function correctly returns "not supported on macOS". No change needed.

---

## credrotation/db_windows.go

Implement `updateDBUserPassword` — psql and mysql CLIs exist on Windows.

```go
func updateDBUserPassword(ctx context.Context, p DBRotateParams) error {
    var stmt string
    switch p.DBEngine {
    case "postgres":
        stmt = fmt.Sprintf("ALTER USER %s WITH PASSWORD '%s';", p.DBUsername, p.NewPassword)
    case "mysql":
        stmt = fmt.Sprintf("ALTER USER '%s'@'%%' IDENTIFIED BY '%s';", p.DBUsername, p.NewPassword)
    default:
        return fmt.Errorf("unsupported db engine: %s", p.DBEngine)
    }
    cmd := buildDBCmd(ctx, p, stmt)
    out, err := cmd.CombinedOutput()
    if err != nil {
        return fmt.Errorf("update db user password: %w — %s", err, out)
    }
    return nil
}

func buildDBCmd(ctx context.Context, p DBRotateParams, stmt string) *exec.Cmd {
    // Same as Linux — psql and mysql CLIs work identically on Windows
    switch p.DBEngine {
    case "postgres":
        return exec.CommandContext(ctx, "psql",
            fmt.Sprintf("host=%s port=%d user=%s sslmode=require", p.DBHost, p.DBPort, p.DBUsername),
            "-c", stmt)
    case "mysql":
        return exec.CommandContext(ctx, "mysql",
            fmt.Sprintf("-h%s", p.DBHost),
            fmt.Sprintf("-P%d", p.DBPort),
            fmt.Sprintf("-u%s", p.DBUsername),
            "-e", stmt)
    default:
        return exec.CommandContext(ctx, "cmd", "/c", "echo", "unsupported")
    }
}
// restartService already implemented via net stop/start — no change needed
```

---

## Build Tag Fixes

All of these need `&& !darwin` added once their respective SP's darwin files exist:

| File | Current tag | New tag |
|------|------------|---------|
| `isolation/isolation_other.go` | `!linux && !windows` | `!linux && !darwin && !windows` |
| `ossecurity/ossecurity_other.go` | `!linux` | `!linux && !darwin` |
| `appdiscovery/appdiscovery_other.go` | `!linux` | `!linux && !darwin` |
| `deepdiscover/deepdiscover_other.go` | `!linux && !windows` | `!linux && !darwin && !windows` |
| `forensics/forensics_other.go` | `!linux && !windows` | `!linux && !darwin && !windows` |
| `linuxauth/linuxauth_other.go` | `!linux` | `!linux && !darwin` |
| `linuxauth/lock_user_other.go` | `!linux && !darwin` already correct | No change |
| `compliance/compliance_other.go` | `!linux && !windows` | `!linux && !darwin && !windows` |

Note: `configsyslog`, `crossplatform`, `estimatesize`, `fleet` all have explicit `_darwin.go` files already (they were stubs) — build tags are already correctly separated; only the function bodies need updating.

---

## Files Modified

| File | Change |
|------|--------|
| `agent/commands/configsyslog/configsyslog_darwin.go` | Replace stub with syslogd implementation |
| `agent/commands/crossplatform/crossplatform_darwin.go` | Replace stubs with TLS/DNS/inventory implementations |
| `agent/commands/estimatesize/estimatesize_darwin.go` | Replace stub with du/df implementation |
| `agent/commands/fleet/fleet_darwin.go` | Replace stubs with launchctl/Go implementations |
| `agent/commands/credrotation/db_windows.go` | Implement updateDBUserPassword |
| 8× `_other.go` files | Add `&& !darwin` to build tag |

---

## Testing

- `configsyslog_darwin_test.go`: mock exec; verify syslog.conf block write and launchctl kickstart called.
- `crossplatform_darwin_test.go`: mock networksetup; verify DNS resolvers set; verify inventory parses brew output.
- `estimatesize_darwin_test.go`: mock du/df; verify size_bytes = parsed_kb × 1024.
- `fleet_darwin_test.go`: mock launchctl kickstart; verify fallback to brew services on error.
- `credrotation/db_windows_test.go`: mock exec; verify correct psql statement for postgres; mysql statement for mysql.
