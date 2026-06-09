# Agent Parity SP2: macOS Isolation & Firewall Design

**Session:** 2026-06-09  
**Status:** Approved

---

## Scope

Two macOS stubs replaced with real implementations:

1. `agent/commands/isolation/isolation_darwin.go` — `isolateOS` / `restoreOS` via `pfctl`
2. `agent/commands/ossecurity/firewall_darwin.go` — `firewallExecuteOS` / `firewallRollbackOS` via `socketfilterfw` + `pfctl`

Both `_other.go` files currently compile on darwin (build tag `!linux && !windows` for isolation, `!linux` for ossecurity). After this SP, `isolation_other.go` build tag changes to `!linux && !darwin && !windows` and `ossecurity_other.go` is addressed in SP7.

---

## isolation_darwin.go

**Strategy:** Mirror the Linux implementation using `pfctl` instead of `iptables`.

macOS ships `pfctl` (Packet Filter, OpenBSD lineage) as the native packet filter. It is always present; no install required. Rules are written to a temp file and loaded with `pfctl -f`. The full ruleset is captured before isolation and restored on rollback.

**Snapshot:** Capture existing rules with `pfctl -s rules` (anchor-less output sufficient for rollback). Store in `PreIsolationState.PFRules` (new field, alongside existing `IPTablesRules`/`NFTablesRules`).

**Isolation ruleset** (written to `/tmp/nexplane-pf-iso.conf`):
```
# Nexplane isolation — allow CP + mgmt only
pass out quick proto tcp to <cp_ip>/32 port 443
pass out quick proto tcp to <mgmt_cidr> port 22
pass out quick on lo0 all
block out all
block in all
```

Load with `pfctl -e -f /tmp/nexplane-pf-iso.conf`. Flush temp file afterwards.

**Rollback:** If `PFRules` is non-empty, write to temp file and `pfctl -f` it. If empty (pfctl was disabled before isolation), run `pfctl -d` to disable pf entirely.

**Shared struct change:** Add `PFRules string` to `PreIsolationState` in `isolation.go`.

**Build tag on isolation_other.go:** Change from `//go:build !linux && !windows` → `//go:build !linux && !darwin && !windows`.

---

## ossecurity/firewall_darwin.go

**Strategy:** Two-layer firewall — `socketfilterfw` for application-layer control (ALF, macOS Application Layer Firewall), `pfctl` for IP/port-level rules.

`socketfilterfw` is Apple's Application Layer Firewall CLI (`/usr/libexec/ApplicationFirewall/socketfilterfw`). It allows/blocks apps by bundle path.

**Snapshot:** Capture `socketfilterfw --getglobalstate` + `socketfilterfw --listapps` output.

**Action dispatch:**
- `action = "add_rule"`: If `rule.app_path` present → `socketfilterfw --add <path> --unblockapp <path>`. If `rule.port` + `rule.protocol` present → add pf anchor rule to `/etc/pf.anchors/nexplane` and load via `pfctl -a nexplane -f`.
- `action = "remove_rule"`: `socketfilterfw --remove <path>` or remove pf anchor rule.
- `action = "flush"`: `socketfilterfw --setglobalstate off` then re-enable; remove pf nexplane anchor.
- `action = "enable"`: `socketfilterfw --setglobalstate on --setstealthmode on`.

**Rollback:** Restore from `snapshot` string — `socketfilterfw --setglobalstate` based on snapshot parse; flush nexplane pf anchor.

**Parameters interface** (same as Linux `rule` map):
```json
{
  "action": "add_rule",
  "rule": {
    "app_path": "/Applications/Foo.app",
    "port": "8080",
    "protocol": "tcp",
    "action_type": "ACCEPT"
  }
}
```

---

## Files Modified

| File | Change |
|------|--------|
| `agent/commands/isolation/isolation.go` | Add `PFRules string` to `PreIsolationState` struct |
| `agent/commands/isolation/isolation_darwin.go` | New — pfctl isolate/restore |
| `agent/commands/isolation/isolation_other.go` | Build tag: add `&& !darwin` |
| `agent/commands/ossecurity/firewall_darwin.go` | New — socketfilterfw + pfctl firewall |

Note: `ossecurity_other.go` build tag fix (remove darwin from `firewallExecuteOS`/`firewallRollbackOS` stubs) happens in SP7 when all ossecurity darwin files exist.

---

## Testing

- `isolation_darwin_test.go`: mock `pfctl` via `execCommand` var (same pattern as deepdiscover); test that snapshot is captured before rules are applied; test rollback restores snapshot content.
- `firewall_darwin_test.go`: mock `socketfilterfw` and `pfctl`; test add_rule, remove_rule, flush actions; test rollback parses globalstate from snapshot.
