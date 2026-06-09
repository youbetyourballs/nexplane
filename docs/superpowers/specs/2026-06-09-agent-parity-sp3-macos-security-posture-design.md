# Agent Parity SP3: macOS Security Posture Design

**Session:** 2026-06-09  
**Status:** Approved

---

## Scope

Eight new `ossecurity` darwin files implementing macOS-native equivalents of Linux security controls:

| Linux | macOS Equivalent | File |
|-------|-----------------|------|
| SELinux | SIP + Gatekeeper + Santa LOCKDOWN | `selinux_darwin.go` |
| AppArmor | Santa binary allowlist rules | `apparmor_darwin.go` |
| seccomp | `sandbox-exec` profiles | `seccomp_darwin.go` |
| sysctl hardening | macOS `sysctl` net.inet subset | `sysctl_darwin.go` |
| kernel module blacklist | `systemextensionsctl` | `modules_darwin.go` |
| mount hardening | `diskutil` + mount options | `mount_darwin.go` |
| auditd rules | BSM audit (`audit_control`) | `auditd_darwin.go` |
| FIM (aide/tripwire) | `fswatch` + digest snapshots | `fim_darwin.go` |

---

## selinux_darwin.go

**Equivalent:** SIP (System Integrity Protection) + Gatekeeper + Santa LOCKDOWN mode. These three together enforce mandatory access control — SIP protects system paths, Gatekeeper enforces signing requirements, Santa LOCKDOWN blocks unapproved binaries.

**Parameters:**
- `mode`: `"enforcing"` → enable Gatekeeper + Santa LOCKDOWN; `"permissive"` → Santa MONITOR mode; `"disabled"` → disable Gatekeeper (requires SIP off, always refuse)
- `generate_from_audit_log`: scan Santa decision log for denials, output summary
- `module_source`: not applicable on macOS (return informational error)

**Snapshot:** `spctl --status` output + `santactl status | grep Mode` + `csrutil status`.

**Execute:**
```go
// Enable Gatekeeper
exec.Command("spctl", "--master-enable").Run()
// Set Santa mode via santactl or rule file
exec.Command("santactl", "rule", "--sync").Run() // triggers sync with configured mode
// SIP can only be toggled from Recovery — document this, don't error
```

For LOCKDOWN: write `/etc/santa/sync_state.json` with `{"mode": "LOCKDOWN"}` and send SIGHUP to santad, or call `santactl sync` if a sync server is configured.

**Rollback:** Restore previous Gatekeeper state (`spctl --master-disable` if it was off), restore Santa mode.

---

## apparmor_darwin.go

**Equivalent:** Santa binary allowlist rules. AppArmor confines processes by profile; Santa confines by binary identity (team-ID, certificate hash, or SHA-256).

**Parameters:** Same interface as Linux — `profile_name`, `profile_content`, `mode`.

**Mapping:**
- `profile_content` = JSON array of Santa rules: `[{"policy": "ALLOWLIST", "sha256": "abc...", "comment": "profile_name"}]`
- `mode = "enforce"` → `santactl rule --allow --sha256 <hash>`  
- `mode = "complain"` → Santa MONITOR mode (logs denials, doesn't block)
- `mode = "disable"` → remove rule: `santactl rule --remove --sha256 <hash>`

**Snapshot:** `santactl rule list --json` output (or `santactl rule list` if JSON flag unavailable).

**Execute:**
```go
// Parse profile_content as [{sha256, team_id, comment}, ...]
// For each rule: santactl rule --allow --sha256 <hash> --comment <comment>
// Or: santactl rule --allow --teamid <team_id> --comment <comment>
```

**Rollback:** Remove rules that were added (by sha256/team_id). Restore snapshot via `santactl rule --remove` for each added entry.

---

## seccomp_darwin.go

**Equivalent:** `sandbox-exec` sandbox profiles (Apple Sandbox / Seatbelt). These are SBPL (Sandbox Profile Language) files that restrict syscall access for processes.

**Parameters:** `service_name`, `profile` (SBPL content as string).

**Profile location:** `/private/etc/nexplane/sandbox/<service_name>.sb`

**Snapshot:** Read existing file at profile path if present.

**Execute:**
```go
os.MkdirAll("/private/etc/nexplane/sandbox", 0755)
os.WriteFile(profilePath, []byte(profile), 0644)
// Write launchd plist drop-in that wraps service with sandbox-exec
dropInContent := fmt.Sprintf(`<?xml version="1.0"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" ...>
<plist version="1.0"><dict>
  <key>ProgramArguments</key><array>
    <string>/usr/bin/sandbox-exec</string>
    <string>-f</string><string>%s</string>
    <string>/path/to/service</string>
  </array>
</dict></plist>`, profilePath)
```

Note: macOS does not support systemd drop-ins. Write the profile file and return its path so the operator can apply it. Document that service restart is manual.

**Rollback:** Restore previous profile content or remove file.

---

## sysctl_darwin.go

**macOS sysctl hardening subset** — mirrors Linux CIS L1 but with darwin-valid keys only.

```go
var cisDarwinSysctl = map[string]string{
    "net.inet.ip.forwarding":          "0",
    "net.inet.ip.redirect":            "0",
    "net.inet6.ip6.forwarding":        "0",
    "net.inet.icmp.bmcastecho":        "0",
    "kern.sugid_coredump":             "0",
    "kern.securelevel":                "1",
    "security.mac.proc_enforce":       "1",
    "security.mac.vnode_enforce":      "1",
}
const darwinSysctlDropIn = "/Library/LaunchDaemons/com.nexplane.sysctl.plist"
```

**Snapshot:** Read existing drop-in plist + current live values via `sysctl -n <key>`.

**Execute:** Apply each key with `sysctl -w key=value`. Write persistence plist (LaunchDaemon that runs `sysctl` on boot).

**Rollback:** Re-apply snapshot values; remove persistence plist.

---

## modules_darwin.go

**Equivalent:** `systemextensionsctl` for System Extensions (kernel module equivalent). macOS deprecated kernel extensions (kexts) in favor of System Extensions (DriverKit, NetworkExtension, EndpointSecurity).

**Parameters:** `modules` ([]string of extension bundle IDs to disable/enable).

**Snapshot:** `systemextensionsctl list` output.

**Execute:**
```go
// Enable developer mode to allow extension control
exec.Command("systemextensionsctl", "developer", "on").Run()
for _, bundleID := range modules {
    // Disable (blacklist equivalent)
    exec.Command("systemextensionsctl", "uninstall", bundleID).Run()
}
```

For traditional kexts (if still used): write to `/Library/SandboxProfiles/` or use `kextstat`/`kextunload`.

**Rollback:** Re-enable extensions from snapshot via `systemextensionsctl reset`.

---

## mount_darwin.go

**macOS mount hardening** — more limited than Linux (`/etc/fstab` is not commonly used; volumes are managed by diskutil/APFS).

**Parameters:** `path`, `options` ([]string).

**Snapshot:** `mount` command output filtered to path.

**Execute:**
```go
// For removable/non-boot volumes: remount with options
exec.Command("mount", "-u", "-o", strings.Join(options, ","), path).Run()
// Note: /System and boot volume cannot be remounted on macOS with SIP
// Return informational result documenting SIP constraint
```

If path is protected by SIP, return `{"skipped": true, "reason": "SIP protects this volume"}` rather than erroring — this is valid security posture information.

**Rollback:** Remount with original options from snapshot.

---

## auditd_darwin.go

**Equivalent:** BSM audit (Basic Security Module). macOS ships BSM audit natively — `/etc/security/audit_control` configures what events are captured.

**Profiles:**
```go
var bsmProfiles = map[string]string{
    "cis_level1": "dir:/var/audit\nflags:lo,aa\nminfree:5\nnaflags:lo\npolicy:cnt,argv\nfilesz:2M\nexpire-after:10M\n",
    "cis_level2": "dir:/var/audit\nflags:lo,aa,ex,pc\nminfree:5\nnaflags:lo\npolicy:cnt,argv,arge\nfilesz:5M\nexpire-after:50M\n",
}
const bsmAuditControl = "/etc/security/audit_control"
```

**Snapshot:** Read `/etc/security/audit_control`.

**Execute:**
```go
os.WriteFile(bsmAuditControl, []byte(rulesContent), 0644)
exec.Command("audit", "-s").Run() // reload config
```

**Rollback:** Restore snapshot content, reload with `audit -s`.

---

## fim_darwin.go

**Equivalent:** `fswatch` (FSEvents wrapper) + periodic SHA-256 digest snapshots. `fswatch` is a macOS-native file system event monitor using the FSEvents API.

**Actions:** `init` (create digest snapshot), `check` (compare current digests to snapshot).

**Tool detection:** Check `fswatch` in PATH (installable via Homebrew, may not be present). Fall back to pure Go `crypto/sha256` + `filepath.Walk` if fswatch absent.

**Snapshot format:** JSON map of `{path: sha256hex}` written to `/var/lib/nexplane/fim-snapshot.json`.

**Execute (init):**
```go
snapshot := map[string]string{}
filepath.Walk(watchPaths, func(path string, info os.FileInfo, err error) error {
    if !info.IsDir() {
        hash := sha256File(path)
        snapshot[path] = hash
    }
    return nil
})
json.Marshal(snapshot) → write to snapshotPath
```

**Execute (check):**
```go
// Read existing snapshot, walk paths again, compare hashes
// Return {violations: [{path, expected, actual}]}
```

**Rollback:** `fim` has no rollback (monitoring-only); return `{"rolled_back": false, "reason": "FIM is monitoring-only"}`.

---

## Files Created

```
agent/commands/ossecurity/selinux_darwin.go
agent/commands/ossecurity/apparmor_darwin.go
agent/commands/ossecurity/seccomp_darwin.go
agent/commands/ossecurity/sysctl_darwin.go
agent/commands/ossecurity/modules_darwin.go
agent/commands/ossecurity/mount_darwin.go
agent/commands/ossecurity/auditd_darwin.go
agent/commands/ossecurity/fim_darwin.go
```

Note: `posture_linux.go` exists — a `posture_darwin.go` will be written in SP5 (compliance audit). `ossecurity_other.go` build tag fix (add `&& !darwin`) is SP7.

---

## Testing

Each file gets a `_test` function using an `execCommand` var (same mockable pattern as `deepdiscover_linux.go`) to avoid root or real tool requirements. Tests verify: snapshot capture happens before execute, execute calls correct OS tool, rollback restores from snapshot.
