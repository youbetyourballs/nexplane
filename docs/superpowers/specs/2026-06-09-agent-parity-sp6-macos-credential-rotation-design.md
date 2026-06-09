# Agent Parity SP6: macOS Credential Rotation Design

**Session:** 2026-06-09  
**Status:** Approved

---

## Scope

`agent/commands/credrotation/db_darwin.go` is a stub returning "not supported on macOS". The database credential rotation logic (psql/mysql CLI) is identical on macOS — the only difference is service restart uses `launchctl` instead of `systemctl`.

---

## db_darwin.go

**Strategy:** Copy Linux implementation verbatim; replace only the service restart mechanism.

```go
//go:build darwin

package credrotation

import (
    "context"
    "fmt"
    "os/exec"
)

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
    switch p.DBEngine {
    case "postgres":
        return exec.CommandContext(ctx,
            "psql",
            fmt.Sprintf("host=%s port=%d user=%s sslmode=require", p.DBHost, p.DBPort, p.DBUsername),
            "-c", stmt,
        )
    case "mysql":
        return exec.CommandContext(ctx,
            "mysql",
            fmt.Sprintf("-h%s", p.DBHost),
            fmt.Sprintf("-P%d", p.DBPort),
            fmt.Sprintf("-u%s", p.DBUsername),
            "-e", stmt,
        )
    default:
        return exec.CommandContext(ctx, "false")
    }
}

func restartService(ctx context.Context, serviceName string) error {
    // Try launchd plist paths in order
    plists := []string{
        "/Library/LaunchDaemons/" + serviceName + ".plist",
        "/System/Library/LaunchDaemons/" + serviceName + ".plist",
    }
    for _, plist := range plists {
        unload := exec.CommandContext(ctx, "launchctl", "unload", plist)
        if out, err := unload.CombinedOutput(); err == nil {
            load := exec.CommandContext(ctx, "launchctl", "load", "-w", plist)
            if out2, err2 := load.CombinedOutput(); err2 != nil {
                return fmt.Errorf("launchctl load %s: %w — %s", plist, err2, out2)
            }
            return nil
        } else {
            _ = out // plist not found at this path, try next
        }
    }
    // Fallback: brew services restart (for Homebrew-managed services)
    cmd := exec.CommandContext(ctx, "brew", "services", "restart", serviceName)
    if out, err := cmd.CombinedOutput(); err != nil {
        return fmt.Errorf("restart %s (tried launchd + brew): %w — %s", serviceName, err, out)
    }
    return nil
}
```

**db_windows.go** is also a stub — checked during SP7 audit; if it follows the same pattern, implement with Windows service restart via `net stop/start <serviceName>` or PowerShell `Restart-Service`.

---

## Files Modified

| File | Change |
|------|--------|
| `agent/commands/credrotation/db_darwin.go` | Replace stub with full implementation |
| `agent/commands/credrotation/db_windows.go` | Replace stub with Windows service restart (if stubbed — confirmed in SP7) |

---

## Testing

`db_darwin_test.go`: mock `exec.Command`; verify correct psql/mysql statement construction; verify launchctl restart called on restart; verify fallback to brew services.
