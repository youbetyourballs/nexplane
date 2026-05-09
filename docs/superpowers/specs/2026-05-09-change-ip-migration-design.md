# IP Address Migration — Design Spec

## Goal

Enable safe, reliable IP address changes for hosts undergoing virtualization migration or microsegmentation subnet standardization. The system automatically selects the safest available method based on host capabilities, falls back gracefully, and guarantees rollback in all failure modes — including loss of control plane connectivity.

---

## Background

The existing `change_ip` agent command applies IP changes in a single step with snapshot-based rollback. It has a fundamental race condition: if the agent's own IP changes, the HTTP connection back to the control plane breaks, the job result is never posted, and rollback is never triggered. DNS configuration is silently ignored despite being in the parameter schema. There is no secondary IP support, no fleet orchestration, and no automatic recovery.

---

## Architecture

### Four Modes

```
Pre-flight intelligence
        │
        ▼
Is Tailscale active? ─── YES ──► Method A: Tailscale-first
        │                         Zero connectivity risk.
        NO                        Change IP freely; agent remains
        │                         reachable via Tailscale overlay IP.
        ▼
New subnet routable? ─── YES ──► Method B: Secondary IP swap
        │                         Two-phase commit. Add secondary,
        NO                        verify reachability, promote, remove old.
        │
        ▼
Commit timer supported? ─YES ──► Method D: Dead man's switch
        │                         Apply change, start timer (default 30s,
        NO                        configurable). Auto-rollback if control
        │                         plane not reached within window.
        ▼
Method C: Multi-step workflow
        Manual operator confirmation at each stage.
```

The `method` parameter on the CR can override auto-selection. Auto is the default.

---

## New Change Types

### `change_ip` (modified — backward-compatible)

Existing change type extended with new parameters. Old behavior (no method specified) defaults to `method: auto`.

**New parameters:**
| Parameter | Type | Default | Description |
|---|---|---|---|
| `method` | string | `"auto"` | `auto`, `tailscale`, `secondary_swap`, `commit_timer`, `manual` |
| `commit_timer_seconds` | integer | `30` | Seconds before auto-rollback fires (commit_timer mode). Min 10, max 300. |
| `add_secondary` | boolean | `false` | Keep old IP as secondary while new IP is primary |
| `dns_servers` | array | `[]` | DNS servers to configure (now actually implemented) |
| `probe_interval_seconds` | integer | `5` | How often the commit timer polls the control plane |
| `preflight_arp_probe` | boolean | `true` | ARP-probe destination IP before applying (detect conflicts) |

**Rollback improvements:**
- Snapshot now includes DNS configuration, all routes, MTU, and secondary addresses
- Rollback restores all of the above, not just the primary IP

---

### `migrate_ip` (new)

Higher-level orchestration CR that wraps `change_ip` with explicit stages, per-stage rollback, and operator confirmation gates.

**Stages:**
1. `preflight` — validate interface, ARP probe, routability check, Tailscale detection
2. `add_secondary` — add new IP as secondary (Method B only)
3. `verify_secondary` — control plane probes new IP (Method B only)
4. `apply_change` — execute the IP swap (calls `change_ip` with appropriate method)
5. `verify_new` — confirm control plane reachable at new address
6. `remove_old` — remove old IP (Method B: final cleanup; other methods: already done)
7. `dns_update` — push DNS changes if provided
8. `commit` — mark change permanent; no further auto-rollback possible

Any stage failure rolls back all previous stages automatically. If `confirm_at_stage` is set, execution pauses and waits for operator approval before continuing.

**Parameters:**
```json
{
  "interface": "eth0",
  "new_ip_v4": "10.10.1.50/24",
  "new_gateway_v4": "10.10.1.1",
  "dns_servers": ["10.10.1.10", "10.10.1.11"],
  "method": "auto",
  "commit_timer_seconds": 30,
  "confirm_at_stage": "verify_new",
  "add_secondary": true,
  "rollback_on_stage_failure": true
}
```

---

### `ip_campaign` (new)

Fleet-level IP migration orchestrator. Applies `migrate_ip` across a set of assets in configurable batches with error thresholds.

**Parameters:**
```json
{
  "target_asset_ids": ["..."],
  "migration_plan": [
    {"asset_id": "...", "interface": "eth0", "new_ip_v4": "10.10.1.50/24"},
    {"asset_id": "...", "interface": "eth0", "new_ip_v4": "10.10.1.51/24"}
  ],
  "batch_size": 5,
  "batch_interval_seconds": 60,
  "abort_error_threshold": 0.1,
  "method": "auto",
  "commit_timer_seconds": 30
}
```

Batches run sequentially. Within a batch, all migrations run in parallel. If `abort_error_threshold` (fraction) of hosts in any batch fail, the campaign halts — remaining batches do not execute. Already-completed batches are not rolled back automatically (too dangerous at fleet scale; operator initiates recovery manually).

---

## Method Details

### Method A: Tailscale-First

**Pre-flight:**
- Confirm Tailscale daemon is active (`tailscale status`)
- Confirm control plane is reachable via Tailscale IP (`curl http://{tailscale_ip}:8000/health`)
- If either fails, fall back to next method

**Execution:**
1. Apply IP change normally (the existing `change_ip` logic)
2. Post result via Tailscale IP (no connectivity risk)
3. After posting: verify new physical IP is reachable (optional health probe)

**Rollback:**
- Agent always reachable via Tailscale IP
- Control plane sends rollback job
- Agent applies rollback and posts result — all via Tailscale

**Notes:**
- This is the preferred method for all Nexplane-managed fleets since Tailscale is a first-class connector
- The commit timer is NOT needed in this mode; connectivity is guaranteed

---

### Method B: Secondary IP Swap (Two-Phase Commit)

**Pre-flight:**
- Probe: can the host route to the control plane via the new subnet? (`ip route get {control_plane_ip} via {new_gateway}`)
- If not routable: fall back to Method D
- ARP probe: verify destination IP is not in use

**Execution:**
1. **Add secondary**: Add new IP to interface alongside existing IP
2. **Probe**: Control plane attempts HTTP to agent at new IP (5 probes over 30s)
3. **If probe succeeds**: Remove old IP (promote new to primary); apply DNS changes
4. **If probe fails within timeout**: Remove secondary; no change to primary

**Rollback (after promotion):**
- Add old IP back as secondary
- Verify reachability
- Remove new IP

**OS support:**
- Linux: `ip addr add {new_ip} dev {interface}` then `ip addr del {old_ip} dev {interface}`
- Windows: `netsh interface ipv4 add address {interface} {new_ip}` then `netsh interface ipv4 delete address {interface} {old_ip}`
- NetworkManager: `nmcli con mod {con} +ipv4.addresses {new_ip}` then remove old

---

### Method D: Dead Man's Switch (Commit Timer)

The agent runs a background goroutine that auto-rolls back the IP change unless the control plane is reached within `commit_timer_seconds`.

**Execution sequence:**

```
1. Write timer state to disk:
   /var/lib/nexplane-agent/pending_rollback.json
   {
     "job_id": "...",
     "expires_at": "2026-05-09T12:30:30Z",
     "rollback_params": {...snapshot...},
     "probe_url": "http://{control_plane_ip}:8000/health",
     "probe_interval_seconds": 5
   }

2. Start background goroutine with timer

3. Apply IP change (existing changip logic)

4. Background goroutine polls control plane every probe_interval_seconds:
   GET http://{control_plane_ip}:8000/health (timeout: 3s)

5a. If HTTP 200 received before timer expires:
    → Delete pending_rollback.json
    → Post success result (using new IP)
    → Cancel timer goroutine

5b. If timer expires without successful probe:
    → Apply rollback from snapshot
    → Delete pending_rollback.json
    → Post failure result (using restored old IP)
```

**Crash recovery:**
On agent startup, check for `pending_rollback.json`. If found and `expires_at` is in the past: apply rollback immediately. If `expires_at` is in the future: resume the commit timer goroutine from the remaining window.

**Configurable parameters:**
- `commit_timer_seconds`: 10–300, default 30
- `probe_interval_seconds`: 1–30, default 5
- `probe_url`: defaults to the agent's configured control plane URL
- `probe_timeout_seconds`: 1–10, default 3

**Notes:**
- The control plane does NOT need to know about the timer — it just needs to respond to health probes
- No special infrastructure required beyond the existing HTTP connection
- Works on any OS/network manager combination
- The timer state on disk survives agent crashes

---

### Method C: Multi-Step Manual Workflow

Used when none of A, B, or D are viable — or when the operator explicitly wants confirmation gates.

Implemented as the `migrate_ip` change type with `confirm_at_stage` set. Each stage appears in the CR status in the UI. The operator reviews the state of the host at the confirmation gate before proceeding.

**When to use:**
- Cross-VLAN migrations where secondary IP routing isn't possible and Tailscale isn't deployed
- High-risk production hosts where a human must verify before each destructive step
- Audit/compliance requirements for change records at each stage

---

## Pre-flight Checks (All Modes)

Executed before any IP change starts:

| Check | Failure action |
|---|---|
| Interface exists on host | Abort — cannot proceed |
| New IP is valid CIDR notation | Abort — bad parameter |
| New IP not already assigned to this interface | Abort — no-op |
| ARP probe: new IP not in use on LAN | Warn + require `force: true` to override |
| New gateway reachable from current config | Warn (non-fatal for static configs) |
| If `add_secondary`: secondary not already configured | Abort |
| If Method A: Tailscale active + control plane reachable | Fall back to next method |
| If Method B: new subnet routable | Fall back to next method |
| DNS servers provided: at least one is resolvable | Warn (non-fatal) |

---

## DNS Configuration (Fixed)

The `dns_servers` parameter is now implemented in the Go agent across all platforms:

**Linux:**
- systemd-resolved: `resolvectl dns {interface} {dns_servers}`
- NetworkManager: `nmcli con mod {con} ipv4.dns "{dns1} {dns2}"`
- `/etc/resolv.conf` fallback: write `nameserver` lines

**Windows:**
- `netsh interface ipv4 set dnsservers name={interface} static {primary_dns} primary`
- `netsh interface ipv4 add dnsservers name={interface} {secondary_dns} index=2`

DNS changes are included in the snapshot and restored during rollback.

---

## IPv6 Support

All four methods support IPv4, IPv6, or both simultaneously:

- **Dual-stack add**: Add IPv6 alongside existing IPv4 (no disruption to IPv4 traffic)
- **IPv6 replace**: Replace existing IPv6 address (same risk model as IPv4)
- **DHCPv6**: Set interface to DHCPv6 (`ip_version: "6"`, `mode: "dhcp"`)
- **SLAAC**: Set interface to SLAAC auto-configuration

When `ip_version: "both"`, the pre-flight checks and commit timer apply to both stacks independently. The commit timer probes via both addresses — success on either cancels the timer.

---

## Rollback Snapshot (Extended)

The snapshot captured before any change now includes:

```json
{
  "interface": "eth0",
  "ip_v4_addresses": ["10.0.0.100/24"],
  "ip_v6_addresses": ["fd00::100/64"],
  "gateway_v4": "10.0.0.1",
  "gateway_v6": "fd00::1",
  "dns_servers": ["10.0.0.10", "10.0.0.11"],
  "dns_search_domains": ["corp.example.com"],
  "routes": [
    {"dst": "10.0.0.0/24", "gw": "", "dev": "eth0"},
    {"dst": "0.0.0.0/0", "gw": "10.0.0.1", "dev": "eth0"}
  ],
  "mtu": 1500,
  "network_manager": "NetworkManager",
  "connection_name": "Wired connection 1"
}
```

Rollback restores all of these, not just the primary address.

---

## UI Changes

### Asset Detail — Network Tab

New tab on asset detail page showing:
- Current IP configuration (addresses, gateway, DNS, routes)
- "Change IP" button → opens single-host migration wizard
- Method selector (auto/tailscale/secondary_swap/commit_timer/manual)
- Commit timer slider (10–300 seconds)
- IP change history (last 5 changes with rollback status)

### IP Migration Wizard (single host)

4 steps:
1. **Configure** — enter new IP, gateway, DNS, select method
2. **Pre-flight** — shows live pre-flight check results; blocked if critical failures
3. **Execute** — live progress per stage; commit timer countdown if Method D
4. **Verify** — connectivity health indicators; confirm or rollback

### Fleet IP Campaign

New page under Operations → IP Campaigns:
- Asset selector (filter by subnet, tag, asset type)
- Migration plan table (per-asset new IP assignment)
- Batch configuration (batch size, interval, error threshold)
- Launch → campaign progress dashboard with per-host status

---

## Smoke Test Coverage

New AWS smoke test phases:

**Phase IP-A (Tailscale-first):**
1. Verify Tailscale active on Phase A instance
2. Fire `change_ip` with `method: tailscale` and a new valid IP in the same subnet
3. Verify agent still reachable via Tailscale IP
4. Verify new IP applied via SSM
5. Rollback and verify original IP restored

**Phase IP-D (Dead man's switch — success path):**
1. Fire `change_ip` with `method: commit_timer`, `commit_timer_seconds: 60`
2. Verify timer file written to disk (`/var/lib/nexplane-agent/pending_rollback.json`)
3. Verify control plane receives probe within 60 seconds
4. Verify new IP committed, timer file deleted

**Phase IP-D2 (Dead man's switch — rollback path):**
1. Change routing to make control plane temporarily unreachable at new IP
2. Fire `change_ip` with `method: commit_timer`, `commit_timer_seconds: 15`
3. Verify timer fires and rollback applied automatically within 20 seconds
4. Verify original IP restored, agent re-connects

**Phase IP-B (Secondary swap):**
1. Verify new subnet routable from test instance
2. Fire `migrate_ip` with `method: secondary_swap`
3. Verify secondary added via SSM during stage 2
4. Verify promotion and old IP removed in stage 3
5. Rollback and verify

---

## DNS Name Awareness and Coordination

### Problem

When an asset's IP changes, any DNS A/AAAA records pointing to the old IP become stale. If TTL is long (e.g., 3600s), clients continue hitting the old IP for up to an hour after the change. If the IP change rolls back, any DNS records already updated point to an IP that no longer works. The order of operations matters and is TTL-dependent.

### DNS Inventory Discovery

Before executing any `change_ip` or `migrate_ip` CR, the backend queries the asset inventory for all DNS names associated with the target asset:

1. **Asset metadata**: `asset_metadata.dns_names[]` — populated by DNS connector ingest (Route53, Azure DNS, GCP Cloud DNS, Cloudflare). Example: `["api.corp.example.com", "web-01.internal.example.com"]`
2. **Asset hostname field**: the asset's registered hostname, which may be a DNS name
3. **Reverse DNS lookup**: query PTR record for the current IP — surfaces DNS names not captured in the connector ingest
4. **Cross-reference**: query each DNS connector for A/AAAA records matching the current IP — catches records that exist but aren't linked to the asset yet

The result is a `dns_records` list attached to the CR context:
```json
{
  "dns_records": [
    {
      "name": "api.corp.example.com",
      "type": "A",
      "value": "10.0.0.100",
      "ttl": 3600,
      "provider": "route53",
      "hosted_zone_id": "Z1234567890",
      "connector_id": "..."
    },
    {
      "name": "web-01.internal.example.com",
      "type": "A",
      "value": "10.0.0.100",
      "ttl": 60,
      "provider": "azure_dns",
      "connector_id": "..."
    }
  ]
}
```

If DNS records are discovered, the CR UI surfaces them for operator review before execution. The operator can include, exclude, or override each record.

### TTL-Aware Ordering

The migration sequence depends on the maximum TTL across all DNS records pointing to the current IP:

**Short TTL (≤ 120s) — single CR:**
```
1. Lower all record TTLs to 60s (if not already ≤ 60s)
2. Wait 60s for propagation (or skip if already short)
3. Change IP (method A/B/D/C)
4. Update DNS A/AAAA records to new IP
5. Restore TTLs to original values
```

**Long TTL (> 120s) — two-CR sequence:**

CR 1: `prepare_dns_for_ip_change`
```
1. For each DNS record: lower TTL to 60s
2. Record original TTLs in snapshot
3. Wait max(original_TTL) seconds — operator-configurable skip
4. Status: "DNS prepared — proceed with migration within 24h"
```

CR 2: `migrate_ip` (must follow CR 1 within `dns_prep_valid_for_hours`, default 24)
```
1. Verify DNS TTLs are still low (re-check, fail if TTLs restored)
2. Validate that CR 1 completed successfully
3. Execute IP change
4. Update DNS records to new IP
5. Restore TTLs (or leave at 60s — operator choice)
```

**Rollback DNS ordering:**
If the IP change rolls back, DNS updates are reverted in reverse order:
1. Revert DNS A/AAAA records to old IP
2. Restore original TTLs
3. Post rollback confirmation

DNS rollback is attempted even if the IP rollback fails — the goal is to prevent split-brain where DNS points to an IP that no longer exists.

### `migrate_ip` Stages (Updated)

The multi-step workflow now includes DNS stages:

```
0. dns_discovery        Discover all DNS names → asset IP mapping
1. preflight            Interface validation, ARP probe, routability
2. dns_prepare          Lower TTLs on all discovered records (skip if short)
3. dns_wait             Wait for TTL propagation (configurable, skip in test mode)
4. add_secondary        Add new IP as secondary (Method B only)
5. verify_secondary     Control plane probes new IP (Method B only)
6. apply_change         Execute the IP swap
7. verify_new           Confirm control plane reachable at new address
8. dns_update           Update A/AAAA records to new IP across all providers
9. dns_verify           Resolve each DNS name and confirm new IP returned
10. remove_old          Remove old IP (Method B cleanup)
11. restore_ttl         Restore TTLs to original values
12. commit              Mark permanent; disable further auto-rollback
```

Stage `confirm_at_stage` defaults to `verify_new` — operator reviews connectivity before DNS is updated.

### DNS Provider Integration

DNS updates are dispatched through existing Nexplane DNS connectors:

| Provider | Connector type | Update mechanism |
|---|---|---|
| AWS Route53 | `aws` | `change_route53_record` CR via route53 executor |
| Azure DNS | `azure` | `update_azure_dns_record` CR |
| GCP Cloud DNS | `gcp` | `update_gcp_dns_record` CR |
| Cloudflare | `cloudflare` | Cloudflare API via connector executor |
| Generic DNS | `dns_zone` | RFC 2136 dynamic DNS update (nsupdate) |
| Internal AD DNS | `active_directory` | `dns_record_update` via AD connector |

Each DNS update is dispatched as a sub-CR within the `migrate_ip` workflow. Sub-CRs are tracked in `step_results`. If any DNS update fails, the migration halts at stage 8 and triggers rollback of all previous DNS changes.

### Asset Metadata Update

After a successful IP change and DNS update:

1. Update `asset.asset_metadata.ip_addresses` to reflect new IP
2. Update `asset.asset_metadata.dns_names` if any names changed (rare — names stay the same, only records update)
3. Write a change event to the audit log: old IP → new IP, DNS records updated

For the `ip_campaign`, a post-campaign asset inventory refresh is triggered automatically to ensure all assets reflect their new IPs.

### Smoke Test: DNS Coordination (Phase IP-DNS)

**Setup:** Target EC2 instance with a Route53 A record pointing to its IP (using a test hosted zone `smoke.nexplane.internal`).

**Steps:**
1. Verify `asset_metadata.dns_names` populated after Route53 ingest
2. Fire `migrate_ip` targeting the EC2 instance with a new IP in the same subnet
3. Verify stage `dns_prepare` lowers Route53 record TTL to 60s
4. Verify stage `dns_update` updates the A record to the new IP
5. Resolve `smoke.nexplane.internal` and assert new IP returned
6. Fire rollback
7. Verify A record reverted to old IP
8. Verify TTL restored

**Rollback-only test:**
1. Configure Route53 A record for the test host
2. Start `migrate_ip` with `commit_timer_seconds: 15` and an invalid gateway (ensures rollback fires)
3. Verify: IP rolled back AND DNS A record rolled back to old IP within 20 seconds

---

## What Is Not In Scope (v1)

- VLAN tagging / 802.1Q
- Bond/team interface configuration
- BGP/OSPF route advertisement
- IPv6 prefix delegation (DHCPv6-PD)
- Autonomous IP selection (AI suggests new IP from available pool)
- Network namespace changes (containers)
