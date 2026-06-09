# Agent Parity SP4: macOS Observability Design

**Session:** 2026-06-09  
**Status:** Approved

---

## Scope

Three observability commands currently return empty/stub results on macOS. This SP implements real darwin versions.

| Command | Current darwin behavior | New darwin implementation |
|---------|------------------------|--------------------------|
| `appdiscovery` | Returns `[]Application{}` (empty) | `launchctl` services + `lsof` ports |
| `deepdiscover` | Returns empty `DeepDiscoveryResult` with `OS: "unsupported"` | `launchctl` workloads + docker + netstat |
| `forensics` | Returns error "not supported on darwin" | `log show` + system logs + ps + netstat bundle |

---

## appdiscovery_darwin.go

**Strategy:** Mirror Linux approach — discover running services from `launchctl`, attach listening ports from `lsof`.

**Service discovery:**
```go
// launchctl list returns: PID  Status  Label
out, _ := exec.Command("launchctl", "list").Output()
// Parse lines: skip header, skip entries with "-" PID (not running)
// For each running service:
//   - launchctl print system/<label> to get ProgramArguments
//   - map label → Application{Name, Binary, Status}
```

**Port discovery:**
```go
// lsof -nP -iTCP -iUDP -sTCP:LISTEN -sUDP
out, _ := exec.Command("lsof", "-nP", "-iTCP", "-sTCP:LISTEN").Output()
// Parse: COMMAND  PID  USER  FD  TYPE  DEVICE  SIZE  NODE  NAME
// NAME format: *:8080 or 127.0.0.1:5432
// Build map[processName][]int{port1, port2}
```

**Non-package binaries:** `mdfind 'kMDItemContentType == "com.apple.application-bundle"'` to discover installed .app bundles, cross-reference running processes from `ps aux`.

**Application struct** (same as Linux — defined in `appdiscovery.go`): populate `Name`, `Binary`, `Version` (from `mdls -name kMDItemVersion`), `ListeningPorts`, `Stateful`.

**Build tag change:** `appdiscovery_other.go` currently `//go:build !linux`. After this SP, change to `//go:build !linux && !darwin`.

---

## deepdiscover_darwin.go

**Strategy:** Mirror Linux implementation using macOS equivalents.

**Workload collection:**

1. **launchctl services** (equivalent to systemd):
```go
func collectLaunchdServicesDarwin() []DiscoveredWorkload {
    out, _ := exec.Command("launchctl", "list").Output()
    // Parse running services (PID != "-")
    // For each: launchctl print to get program path
    return workloads
}
```

2. **Container runtimes** (same as Linux — docker/containerd/podman CLIs work on macOS):
```go
func detectContainerRuntimesDarwin() []DiscoveredWorkload {
    // Same as Linux: docker ps --format json, containerd ctr containers list
}
```

3. **Listening ports** via `netstat -an -p tcp`:
```go
func collectListeningPortsDarwin() map[string][]int {
    out, _ := exec.Command("netstat", "-an", "-p", "tcp").Output()
    // Parse: tcp4  0  0  *.8080  *.*  LISTEN
}
```

4. **Established connections:**
```go
func collectEstablishedConnsDarwin() []NetworkConnection {
    out, _ := exec.Command("netstat", "-an", "-p", "tcp").Output()
    // Filter ESTABLISHED lines
}
```

**Result:** `DeepDiscoveryResult{Workloads, HybridEdges, CollectedAt, OS: "darwin"}`.

**Build tag change:** `deepdiscover_other.go` currently `//go:build !linux && !windows`. After this SP, change to `//go:build !linux && !darwin && !windows`.

---

## forensics_darwin.go

**Strategy:** Collect macOS-specific forensic artifacts into a gzip tar bundle, same structure as Linux.

**Artifact collection:**
```go
func collectOS(ctx context.Context, cfg ForensicsConfig) ([]ArtifactEntry, bytes.Buffer, error) {
    // 1. Process list
    add("ps_aux.txt", runCmd("ps", "aux"))
    
    // 2. Network state
    add("netstat_an.txt", runCmd("netstat", "-an"))
    add("lsof_net.txt", runCmd("lsof", "-nP", "-i"))
    
    // 3. Unified log (last 1 hour, error+fault level)
    add("unified_log.txt", runCmd("log", "show", "--last", "1h", 
        "--predicate", "eventType == logEvent AND messageType >= error",
        "--style", "syslog"))
    
    // 4. System info
    add("sw_vers.txt", runCmd("sw_vers"))
    add("system_profiler.txt", runCmd("system_profiler", "SPSoftwareDataType", "SPHardwareDataType"))
    
    // 5. Launch daemons/agents inventory
    add("launchctl_list.txt", runCmd("launchctl", "list"))
    
    // 6. Loaded kexts
    add("kextstat.txt", runCmd("kextstat"))
    
    // 7. Santa decisions (last 100 if santa present)
    if _, err := exec.LookPath("santactl"); err == nil {
        add("santa_decisions.txt", runCmd("santactl", "fileinfo", "--json", "--output", "/tmp/santa_log_dummy"))
        // Actually: santactl log --last 100 if available
    }
    
    // 8. Security settings
    add("spctl_status.txt", runCmd("spctl", "--status"))
    add("csrutil_status.txt", runCmd("csrutil", "status"))
    
    // 9. Users
    add("dscl_users.txt", runCmd("dscl", ".", "-list", "/Users"))
    
    // 10. Open files (condensed)
    add("lsof_all.txt", runCmd("lsof"))
}
```

**Size cap:** Same `maxBundleSize = 2GB` as Linux. `log show` output is truncated via `--last 1h` to avoid unbounded growth.

**Build tag change:** `forensics_other.go` currently `//go:build !linux && !windows`. After this SP, change to `//go:build !linux && !darwin && !windows`.

---

## Files Created/Modified

| File | Change |
|------|--------|
| `agent/commands/appdiscovery/appdiscovery_darwin.go` | New — launchctl + lsof |
| `agent/commands/appdiscovery/appdiscovery_other.go` | Build tag: add `&& !darwin` |
| `agent/commands/deepdiscover/deepdiscover_darwin.go` | New — launchctl + docker + netstat |
| `agent/commands/deepdiscover/deepdiscover_other.go` | Build tag: add `&& !darwin` |
| `agent/commands/forensics/forensics_darwin.go` | New — log show + system artifacts bundle |
| `agent/commands/forensics/forensics_other.go` | Build tag: add `&& !darwin` |

---

## Testing

- Mock `exec.Command` via package-level var (same pattern as `deepdiscover_linux.go` uses `execCommandLinux`).
- `appdiscovery_darwin_test.go`: inject mock `launchctl list` and `lsof` output; verify Application fields populated.
- `deepdiscover_darwin_test.go`: inject mock outputs; verify workload count and OS field = "darwin".
- `forensics_darwin_test.go`: verify artifact names present in bundle; verify bundle is valid gzip tar.
