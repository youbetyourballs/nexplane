# Agent Parity SP5: macOS Auth & Compliance Design

**Session:** 2026-06-09  
**Status:** Approved

---

## Scope

Six `linuxauth` functions + `compliance` have `_other.go` stubs on macOS. Many have real macOS equivalents. This SP implements darwin versions where equivalents exist, and documents where they don't.

| Function | macOS Equivalent | Action |
|----------|-----------------|--------|
| `pamExecuteOS` / `pamRollbackOS` | macOS `/etc/pam.d/` (PAM is present on macOS) | Implement |
| `sshExecuteOS` / `sshRollbackOS` | `/etc/ssh/sshd_config` (identical) | Implement |
| `certsExecuteOS` / `certsRollbackOS` | `security add-trusted-cert` Keychain | Implement |
| `ntpExecuteOS` / `ntpRollbackOS` | `sntp` + `/etc/ntp.conf` or `systemsetup -getnetworktimeserver` | Implement |
| `auditUsersOS` | `dscl` + `id` + `groups` | Implement |
| `auditPrivescOS` | `sudo -l` + sudoers parse | Implement |
| `auditCISComplianceOS` | macOS CIS benchmark subset | Implement |
| `collectEvidenceOS` | macOS system_profiler + security commands | Implement |

---

## linuxauth_darwin.go — PAM

macOS ships with PAM. Config files live in `/etc/pam.d/<service>`. The password policy is controlled via `pwpolicy` (Directory Services).

**pamExecuteOS:**
```go
func pamExecuteOS(params map[string]any) (map[string]any, error) {
    // Read params (minLen, complexity, maxFailed, lockoutDuration)
    minLen, _ := params["min_length"].(float64)
    maxFailed, _ := params["max_failed_attempts"].(float64)
    
    // Snapshot: read /etc/pam.d/login and /etc/pam.d/sudo
    snapshot := map[string]string{}
    for _, svc := range []string{"login", "sudo", "su"} {
        data, _ := os.ReadFile("/etc/pam.d/" + svc)
        snapshot[svc] = string(data)
    }
    
    // Apply password policy via pwpolicy
    if minLen > 0 {
        exec.Command("pwpolicy", "-setaccountpolicies", 
            fmt.Sprintf(`<dict><key>minChars</key><integer>%d</integer></dict>`, int(minLen))).Run()
    }
    if maxFailed > 0 {
        exec.Command("pwpolicy", "-setaccountpolicies",
            fmt.Sprintf(`<dict><key>maxFailedLoginAttempts</key><integer>%d</integer></dict>`, int(maxFailed))).Run()
    }
    
    return map[string]any{
        "snapshot":    snapshot,
        "applied_at":  time.Now().UTC().Format(time.RFC3339),
        "min_length":  minLen,
        "max_failed":  maxFailed,
    }, nil
}
```

**pamRollbackOS:** Restore `/etc/pam.d/<service>` files from snapshot; call `pwpolicy -clearaccountpolicies` to reset policy.

---

## linuxauth_darwin.go — SSH

`/etc/ssh/sshd_config` is identical on macOS. The only difference: restart via `launchctl` instead of `systemctl`.

```go
func sshExecuteOS(params map[string]any) (map[string]any, error) {
    // IDENTICAL to linux implementation except:
    // - restart: launchctl unload /System/Library/LaunchDaemons/ssh.plist && launchctl load ...
    // - config path: /etc/ssh/sshd_config (same)
    // - validation: sshd -t (same)
    configPath := "/etc/ssh/sshd_config"
    snapshot, _ := os.ReadFile(configPath)
    // Apply sshDefaults map (same as linux) + param overrides
    // Write config, validate with sshd -t, restart sshd
    exec.Command("launchctl", "unload", "/System/Library/LaunchDaemons/ssh.plist").Run()
    exec.Command("launchctl", "load", "-w", "/System/Library/LaunchDaemons/ssh.plist").Run()
    return result, nil
}
```

The `sshDefaults` map and `sshParamMap` from `ssh_linux.go` are duplicated in the darwin file (they are identical — DRY would require moving to `linuxauth.go` but we defer that refactor per YAGNI).

**sshRollbackOS:** Restore config from snapshot, restart sshd.

---

## linuxauth_darwin.go — Certificates

```go
func certsExecuteOS(params map[string]any) (map[string]any, error) {
    action, _ := params["action"].(string)   // "add" or "remove"
    certPath, _ := params["cert_path"].(string)
    certData, _ := params["cert_pem"].(string)
    
    // If cert_pem provided, write to temp file
    tmpPath := "/tmp/nexplane-cert.pem"
    if certData != "" {
        os.WriteFile(tmpPath, []byte(certData), 0644)
        certPath = tmpPath
        defer os.Remove(tmpPath)
    }
    
    switch action {
    case "add":
        // Add to System Keychain trusted roots
        out, err := exec.Command("security", "add-trusted-cert", "-d",
            "-r", "trustRoot", "-k", "/Library/Keychains/System.keychain", certPath).CombinedOutput()
        if err != nil {
            return nil, fmt.Errorf("security add-trusted-cert: %s: %w", out, err)
        }
    case "remove":
        out, err := exec.Command("security", "delete-certificate",
            "-c", certPath, "/Library/Keychains/System.keychain").CombinedOutput()
        if err != nil {
            return nil, fmt.Errorf("security delete-certificate: %s: %w", out, err)
        }
    }
    return map[string]any{"action": action, "cert_path": certPath}, nil
}
```

**Rollback:** Reverse action (remove if added, re-add if removed — cert data preserved in result).

---

## linuxauth_darwin.go — NTP

```go
func ntpExecuteOS(params map[string]any) (map[string]any, error) {
    servers, _ := params["servers"].([]any)
    
    // Snapshot: current time server
    snapOut, _ := exec.Command("systemsetup", "-getnetworktimeserver").Output()
    snapshot := strings.TrimSpace(string(snapOut))
    
    // Enable NTP
    exec.Command("systemsetup", "-setusingnetworktime", "on").Run()
    
    if len(servers) > 0 {
        primary, _ := servers[0].(string)
        exec.Command("systemsetup", "-setnetworktimeserver", primary).Run()
    }
    
    return map[string]any{"snapshot": snapshot, "applied": true}, nil
}
```

**Rollback:** `systemsetup -setnetworktimeserver <snapshot>`.

---

## linuxauth_darwin.go — Users & Privesc Audit

```go
func auditUsersOS(_ map[string]any) (map[string]any, error) {
    // List all users with UID >= 500 (local accounts)
    out, _ := exec.Command("dscl", ".", "-list", "/Users", "UniqueID").Output()
    // Filter, return {username, uid, groups, shell, home}
    // dscl . -read /Users/<username> UserShell PrimaryGroupID
}

func auditPrivescOS(_ map[string]any) (map[string]any, error) {
    // Parse /etc/sudoers and /etc/sudoers.d/*
    // Report users with NOPASSWD, ALL=(ALL) ALL entries
    // Check for setuid binaries: find / -perm -4000 2>/dev/null
    // Return {sudoers_entries, setuid_binaries, risky_entries}
}
```

---

## compliance_darwin.go

macOS CIS benchmark (macOS Security Benchmark v3.0) subset. Functions mirror Linux `compliance_linux.go` structure.

```go
func auditCISComplianceOS(level int, osFamily string) (map[string]any, error) {
    var controls []ControlResult
    
    // Level 1
    controls = append(controls, checkMacOSSoftwareUpdate()...)
    controls = append(controls, checkMacOSGatekeeper()...)
    controls = append(controls, checkMacOSSIP()...)
    controls = append(controls, checkMacOSFirewall()...)
    controls = append(controls, checkMacOSSSH()...)
    controls = append(controls, checkMacOSSysctl()...)
    
    // Level 2
    if level >= 2 {
        controls = append(controls, checkMacOSAudit()...)
        controls = append(controls, checkMacOSSanta()...)
        controls = append(controls, checkMacOSScreenLock()...)
    }
    
    score := CalculateScore(controls)
    return map[string]any{
        "level": level, "os_family": "darwin",
        "score": score, "controls": controls,
        "collected_at": collectedNow(),
    }, nil
}

func checkMacOSSIP() []ControlResult {
    out, _ := exec.Command("csrutil", "status").Output()
    enabled := strings.Contains(string(out), "enabled")
    return []ControlResult{{
        ID: "1.1", Title: "Ensure SIP is enabled",
        Passed: enabled, Actual: strings.TrimSpace(string(out)),
    }}
}

func checkMacOSGatekeeper() []ControlResult {
    out, _ := exec.Command("spctl", "--status").Output()
    enabled := strings.Contains(string(out), "assessments enabled")
    return []ControlResult{{
        ID: "1.2", Title: "Ensure Gatekeeper is enabled",
        Passed: enabled, Actual: strings.TrimSpace(string(out)),
    }}
}

func checkMacOSFirewall() []ControlResult {
    out, _ := exec.Command("/usr/libexec/ApplicationFirewall/socketfilterfw",
        "--getglobalstate").Output()
    enabled := strings.Contains(string(out), "enabled")
    return []ControlResult{{
        ID: "2.1", Title: "Ensure Application Firewall is enabled",
        Passed: enabled, Actual: strings.TrimSpace(string(out)),
    }}
}

// Additional checks: software update pending, screen saver lock,
// remote login (SSH) disabled unless intentional, remote management, etc.
```

`collectEvidenceOS`: Run `system_profiler SPSoftwareDataType SPSecurityDataType` + key file reads + security CLI outputs; return structured map.

**Build tag changes:**
- `compliance_other.go`: `//go:build !linux && !windows` → `//go:build !linux && !darwin && !windows`

---

## Files Created/Modified

| File | Change |
|------|--------|
| `agent/commands/linuxauth/linuxauth_darwin.go` | New — pam, ssh, certs, ntp, users, privesc darwin implementations |
| `agent/commands/linuxauth/linuxauth_other.go` | Build tag: add `&& !darwin` |
| `agent/commands/compliance/compliance_darwin.go` | New — macOS CIS benchmark |
| `agent/commands/compliance/compliance_other.go` | Build tag: add `&& !darwin` |

---

## Testing

- `linuxauth_darwin_test.go`: mock exec calls; test SSH config write + sshd restart; test NTP server set; test user audit output parsing.
- `compliance_darwin_test.go`: mock `csrutil`, `spctl`, `socketfilterfw` outputs; verify ControlResult pass/fail fields.
