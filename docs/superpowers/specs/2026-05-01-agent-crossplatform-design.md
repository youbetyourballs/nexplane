# Agent: Cross-Platform Commands — Design Spec (Spec 5d)

**Date:** 2026-05-01
**Status:** Approved
**Scope:** New agent commands that run on both Linux and Windows — TLS certificate lifecycle management (deploy, renew, validate), DNS resolver configuration (DoH/DoT/internal DNS), and software inventory ingest. All become catalog actions in `nexplane_agent_mock.json`.

---

## Design Decisions

- **Cross-platform implementations:** Each command has both a Linux and Windows implementation file, sharing a common interface defined in the package entry file. Build tags (`//go:build linux` / `//go:build windows`) route to the correct implementation.
- **Rollback contract:** Every `Execute` that modifies system state captures a snapshot before changes, stores it in the result under `"snapshot"`, and implements `Rollback` that reads `snapshot` from merged params and restores prior state.
- **Read-only ingest:** `audit_software_inventory` is read-only. No rollback needed.
- **Catalog integration:** All commands get `nexplane_agent_mock.json` catalog entries and mock executor stubs.

---

## Section 1: TLS Certificate Lifecycle

### 1.1 `manage_tls_certificates`

**Parameters:**
- `action` (string, required) — `deploy`, `renew`, or `validate`
- `service` (string, required) — target service identifier: `nginx`, `apache`, `iis`, `custom`
- `source` (string, required for `deploy`/`renew`) — `acme`, `internal_ca`, or `manual`
- `domain` (string, required for `acme`) — FQDN for the certificate
- `acme_email` (string, required for `acme`) — contact email for Let's Encrypt account
- `acme_challenge` (string, default `http-01`) — `http-01` or `dns-01`
- `csr_pem` (string, optional for `internal_ca`) — PEM-encoded CSR; if omitted, agent generates a key pair and CSR
- `signed_cert_pem` (string, optional for `internal_ca`) — PEM-encoded signed certificate (provided by operator after CA signs the CSR); required when finalizing `internal_ca` flow
- `cert_pem` (string, required for `manual`) — PEM-encoded certificate
- `key_pem` (string, required for `manual`) — PEM-encoded private key
- `chain_pem` (string, optional) — PEM-encoded intermediate chain
- `cert_path` (string, optional for `custom` service) — path where cert should be written
- `key_path` (string, optional for `custom` service) — path where key should be written

**Preflight:**
- Checks caller is root (Linux) or Administrator (Windows)
- For `acme`: checks `certbot` or `acme.sh` is installed (Linux); checks ACME client availability (Windows)
- For `validate`: checks `openssl` (Linux) or `certutil` (Windows) is available
- For `iis`: checks `WebAdministration` PowerShell module is available

**Execute (deploy / renew):**
1. Snapshot: copy existing cert and key files at target paths; record service config state
2. For `acme` + `http-01`: run `certbot certonly --webroot -d {domain} --email {email} --agree-tos -n` (Linux) or equivalent; returns cert path
3. For `acme` + `dns-01`: run `certbot certonly --manual --preferred-challenges dns` with hook scripts — requires DNS API integration; returns cert path
4. For `internal_ca`: if no CSR provided, generate key pair + CSR; return CSR in result for operator to sign; when `signed_cert_pem` provided, write it to the service path
5. For `manual`: write `cert_pem`, `key_pem`, `chain_pem` to service-appropriate paths
6. Install cert to service:
   - `nginx`: write to `/etc/nginx/ssl/{domain}/` and reload nginx
   - `apache`: write to `/etc/ssl/certs/{domain}/` and update VirtualHost, reload apache2
   - `iis`: `Import-PfxCertificate` to cert store, `Set-WebBinding` to update binding
   - `custom`: write to `cert_path` / `key_path`
7. For `validate`: run `openssl x509 -noout -dates -subject -issuer` (Linux) or `certutil -verify` (Windows); return expiry, subject, issuer, SANs

**Result:** `{ action, service, cert_subject, cert_expiry, cert_path, key_path, csr_pem (if generated), snapshot }`

**Rollback:**
- Restore cert and key files from snapshot
- Reload the service (`systemctl reload nginx` / `Restart-WebItem` etc.)

**Catalog entry:**
```json
{
  "action_id": "manage_tls_certificates",
  "action_type": "change",
  "execution_tier": 3,
  "applicable_asset_types": ["server"],
  "rollback_action": "manage_tls_certificates",
  "safety_notes": [
    "ACME http-01 challenge requires port 80 to be publicly reachable — verify before running",
    "Private key is included in the result for manual source — store the job result securely",
    "Certificate renewal via ACME replaces the existing certificate — ensure downtime window if service restart is required"
  ]
}
```

---

## Section 2: DNS Resolver Configuration

### 2.2 `configure_dns_resolver`

**Parameters:**
- `resolvers` (list of strings, required) — DNS server addresses (e.g. `["1.1.1.1", "8.8.8.8"]`)
- `mode` (string, default `plain`) — `plain`, `doh` (DNS-over-HTTPS), `dot` (DNS-over-TLS)
- `doh_url` (string, required for `mode=doh`) — full DoH URL (e.g. `https://1.1.1.1/dns-query`)
- `dot_port` (int, default `853`) — port for DoT
- `search_domains` (list of strings, optional) — DNS search suffixes
- `fallback_to_plain` (bool, default `false`) — allow fallback to plain DNS if encrypted DNS fails

**Preflight:**
- Checks caller is root (Linux) or Administrator (Windows)
- For `doh`/`dot` on Linux: checks `systemd-resolved` is running or `stubby` is installed
- For `doh` on Windows: checks Windows version ≥ Windows 11 / Server 2022 (native DoH support)

**Execute (Linux):**
1. Snapshot: copy `/etc/resolv.conf`, `/etc/systemd/resolved.conf` (if present), `/etc/stubby/stubby.yml` (if present)
2. For `plain`: update `/etc/resolv.conf` with `nameserver` entries + `search` domains within nexplane-managed block; or update `[Resolve]` in `systemd-resolved.conf`
3. For `doh`/`dot` via `systemd-resolved` (v239+): set `DNS=`, `DNSOverTLS=yes`/`opportunistic`/`no` in `resolved.conf`; `systemctl restart systemd-resolved`
4. For `doh`/`dot` via `stubby`: write `/etc/stubby/stubby.yml` with resolver entries and tls configuration within nexplane-managed block; `systemctl restart stubby`

**Execute (Windows):**
1. Snapshot: `netsh dns show server` output + registry export of `HKLM\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters`
2. For `plain`: `Set-DnsClientServerAddress -InterfaceAlias * -ServerAddresses {resolvers}` (applies to all interfaces) + set search suffixes via registry
3. For `doh` (Windows 11/2022+): `Add-DnsClientDohServerAddress -ServerAddress {resolver} -DohTemplate {doh_url}`; `Set-DnsClientDohServerAddress -ServerAddress {resolver} -AllowFallbackToUdp {fallback}`
4. For `dot`: configure via `netsh dns add encryption` if available; otherwise configure via `stubby` for Windows

**Result:** `{ mode, resolvers, search_domains, daemon (Linux), snapshot }`

**Rollback (Linux):** Restore `/etc/resolv.conf` and service config files from snapshot; restart resolved/stubby.

**Rollback (Windows):** Restore from snapshot (registry + `Set-DnsClientServerAddress`); remove DoH entries if added.

**Catalog entry:**
```json
{
  "action_id": "configure_dns_resolver",
  "action_type": "change",
  "execution_tier": 3,
  "applicable_asset_types": ["server", "workstation"],
  "rollback_action": "configure_dns_resolver",
  "safety_notes": [
    "Switching to encrypted DNS may break lookups if the DoH/DoT provider is unreachable — configure fallback_to_plain=true for initial rollout",
    "On Linux, /etc/resolv.conf may be managed by DHCP client (dhclient/NetworkManager) and overwritten on lease renewal — set immutable flag or configure via resolved.conf instead"
  ]
}
```

---

## Section 3: Software Inventory Ingest

### 3.1 `audit_software_inventory` (read-only ingest)

No parameters required.

**Preflight:** Checks caller is root (Linux) or Administrator (Windows) — required for accurate system-wide package enumeration.

**Checks performed (Linux):**

| Method | Scope |
|--------|-------|
| `dpkg-query -W -f='${Package}\t${Version}\t${Status}\n'` | Debian/Ubuntu installed packages |
| `rpm -qa --queryformat '%{NAME}\t%{VERSION}-%{RELEASE}\t%{ARCH}\n'` | RHEL/CentOS/Fedora packages |
| `snap list` | Snap packages |
| `flatpak list --columns=application,version` | Flatpak apps |
| `pip3 list --format=json` | Python packages (system) |
| `gem list` | Ruby gems |
| `npm list -g --depth=0 --json` | Node.js global packages |

**Checks performed (Windows):**

| Method | Scope |
|--------|-------|
| Registry uninstall keys (`HKLM\...\Uninstall\*`, `HKCU\...\Uninstall\*`) | Installed programs |
| `Get-WindowsFeature` / `Get-WindowsOptionalFeature` | Windows roles and features |
| `Get-AppxPackage` | Microsoft Store / MSIX apps |
| `winget list` | winget-managed packages (if available) |

**Result:** Structured package list with `{ name, version, source, architecture }` per entry. Enriches asset `asset_metadata` with `software_inventory` key (timestamp + list). Adds `software-inventory-stale` tag if inventory is older than 7 days on subsequent runs (triggering refresh). Surfaces packages with known high-severity CVEs if Tenable findings exist for the same asset (cross-referenced by asset ID).

No rollback — read-only.

**Catalog entry:**
```json
{
  "action_id": "audit_software_inventory",
  "action_type": "ingest",
  "execution_tier": 3,
  "applicable_asset_types": ["server", "workstation"]
}
```

---

## Section 4: New Files

| File | Purpose |
|------|---------|
| `agent/commands/crossplatform/crossplatform.go` | Package entry: `Execute`/`Rollback` dispatch |
| `agent/commands/crossplatform/tls_linux.go` | `manage_tls_certificates` Linux implementation |
| `agent/commands/crossplatform/tls_windows.go` | `manage_tls_certificates` Windows implementation |
| `agent/commands/crossplatform/dns_linux.go` | `configure_dns_resolver` Linux implementation |
| `agent/commands/crossplatform/dns_windows.go` | `configure_dns_resolver` Windows implementation |
| `agent/commands/crossplatform/inventory_linux.go` | `audit_software_inventory` Linux implementation |
| `agent/commands/crossplatform/inventory_windows.go` | `audit_software_inventory` Windows implementation |
| `agent/commands/crossplatform/crossplatform_test.go` | Validation tests (param validation — no build tag needed) |

## Section 5: Modified Files

| File | Change |
|------|--------|
| `agent/executor/executor.go` | Add 3 new commands to `commands` and `rollbacks` maps |
| `backend/app/connectors/catalog/nexplane_agent_mock.json` | Add 3 catalog entries |
| `backend/app/connectors/executors/nexplane_agent_mock/` | Add 3 mock executor stubs |
