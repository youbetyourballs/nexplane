# Nexplane Agent — Design Spec (Spec 4)

**Date:** 2026-05-01
**Status:** Approved
**Scope:** A cross-platform Go agent (execution tier 3) that runs on managed machines, initiates outbound connections to the Nexplane control plane, receives cryptographically signed commands, executes them, and reports results. Includes the backend agent API, new DB models, and connector catalog integration. Implements five initial generic commands with rollbacks.

---

## Problem Statement

Nexplane's current remote execution path (tier 5 SSH) requires the control plane to initiate inbound connections to managed machines. This increases the attack surface of those machines and fails in environments where inbound SSH is blocked by policy. The Nexplane agent reverses the connection direction: the agent runs on the machine and reaches out to the control plane, receiving signed commands over a long-poll HTTP channel. This is safer, firewall-friendly, and works in locked-down environments.

---

## Decisions

- **Language:** Go — single static binary, no runtime dependency, trivially cross-compiled for `linux/amd64`, `linux/arm64`, `windows/amd64`.
- **Protocol:** Long-poll HTTP — agent calls `GET /agent/jobs/next`, server holds the connection up to 30 seconds. Simple, debuggable, works through enterprise proxies and firewalls.
- **Authentication:** Shared HMAC-SHA256 secret. The agent presents it as a bearer token when polling. The backend signs each job payload; the agent verifies the signature before executing. Protects against MITM and compromised DB.
- **Machine identity:** Stable fingerprint — OS UUID (Windows `MachineGuid` registry, Linux `/etc/machine-id`), falling back to SHA256 of sorted non-loopback MAC addresses. IP address is metadata only.
- **Registration:** Agent registers itself on startup and periodically. Creates/updates an `Asset` (type: `server`) and an `AgentRegistration` row in the backend DB.
- **Modes:** `ephemeral` (invoked via SSH, executes one job, exits) and `service` (persistent, loops with configurable poll interval).
- **Backend integration:** Dedicated `/agent/...` endpoints, separate from the connector system. The `nexplane_agent` connector catalog references executor modules that create `AgentJob` rows; the agent picks them up via polling.
- **Agent location in repo:** `agent/` subdirectory of the Nexplane monorepo. Separate Go module (`go.mod`), compiled independently.

---

## Section 1: Agent Binary

### 1.1 Repository Layout

```
agent/
  go.mod                          # module nexplane-agent
  main.go                         # entry point, flag/env parsing, mode dispatch
  config/
    config.go                     # Config struct, LoadConfig()
  fingerprint/
    fingerprint.go                # GetMachineID() — cross-platform
    fingerprint_linux.go          # reads /etc/machine-id
    fingerprint_windows.go        # reads MachineGuid registry key
    fingerprint_fallback.go       # MAC address hash fallback
  registration/
    registration.go               # Register(), refresh loop
  poller/
    poller.go                     # RunEphemeral(), RunService()
  hmac/
    hmac.go                       # VerifyJobSignature(secret, job)
  executor/
    executor.go                   # Dispatch(command, params) — routes to command packages
  commands/
    estimate_image_size/
      estimate.go                 # Execute(), no rollback
      estimate_linux.go
      estimate_windows.go
    change_ip/
      change_ip.go                # Execute(), Rollback()
      change_ip_linux.go
      change_ip_windows.go
    configure_syslog/
      configure_syslog.go         # Execute(), Rollback()
      configure_syslog_linux.go
      configure_syslog_windows.go
    virtualize/
      virtualize.go               # Execute(), Rollback()
      virtualize_linux.go
      virtualize_windows.go
    upload_image/
      upload.go                   # Execute(), Rollback()
  client/
    client.go                     # HTTP client, bearer token, Register(), Poll(), PostResult()
```

### 1.2 Configuration

All flags have environment variable equivalents. Environment variables take precedence over defaults; flags take precedence over environment variables.

| Flag | Env var | Default | Purpose |
|---|---|---|---|
| `--control-plane` | `NP_CONTROL_PLANE` | (required) | Base URL of Nexplane backend |
| `--secret` | `NP_SECRET` | (required) | Shared HMAC secret |
| `--mode` | `NP_MODE` | `service` | `ephemeral` or `service` |
| `--poll-interval` | `NP_POLL_INTERVAL` | `30s` | Poll interval in service mode |

### 1.3 Machine Fingerprint

`fingerprint.GetMachineID()` tries in order:

1. **Windows:** `HKLM\SOFTWARE\Microsoft\Cryptography\MachineGuid` (string registry value)
2. **Linux:** `/etc/machine-id` (trimmed first line)
3. **Fallback (both):** SHA256 of sorted, concatenated non-loopback MAC addresses, hex-encoded

The fingerprint is stable across reboots, network reconfigurations, and IP changes.

### 1.4 Registration

On startup and every 5 minutes thereafter, the agent calls `POST /agent/register` with:

```json
{
  "machine_id": "...",
  "hostname": "...",
  "os_type": "linux|windows",
  "os_version": "Ubuntu 22.04 / Windows Server 2022",
  "ip_addresses": ["10.0.1.50", "fe80::1"],
  "agent_version": "0.1.0"
}
```

The response provides `agent_id` (the `AgentRegistration` UUID) and `asset_id`. Both are cached in memory for the duration of the process.

### 1.5 Poll Loop

```
loop:
  GET /agent/jobs/next?agent_id={agent_id}
    Authorization: Bearer {secret}

  → 200 { job_id, command, parameters, hmac_signature }
      Verify HMAC signature. If invalid: POST result failed, continue.
      Execute command.
      POST /agent/jobs/{job_id}/result

  → 204  No pending jobs. Wait poll_interval, retry.

  → 4xx/5xx  Log error. Wait poll_interval, retry.
```

In **ephemeral mode**: perform one registration, then poll once. If 204 (no job), exit 0. If a job is received, execute it and post the result, then exit 0. Exit 1 on any error.

In **service mode**: loop indefinitely. On network error, apply exponential backoff (1s → 2s → 4s … max 5 minutes).

### 1.6 HMAC Verification

Before executing any job, the agent verifies:

```
expected = HMAC-SHA256(secret, job_id + ":" + command + ":" + canonical_json(parameters))
```

`canonical_json` means keys sorted alphabetically, no whitespace. If `expected != hmac_signature` (constant-time comparison), the agent posts a failed result with `error: "signature verification failed"` and does not execute the command.

---

## Section 2: Backend Agent API

### 2.1 New DB Models (Migration 006)

**`AgentRegistration`**

| Field | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `organization_id` | UUID FK → Organization | |
| `machine_id` | String 255, unique per org | OS fingerprint |
| `asset_id` | UUID FK → Asset | Created/updated on each registration |
| `hostname` | String 255 | |
| `os_type` | Enum `windows\|linux` | |
| `ip_addresses` | JSON | List of IP strings, updated each registration |
| `os_version` | String 255 | |
| `agent_version` | String 50 | |
| `last_seen` | DateTime(tz) | Updated on every poll |
| `created_at` | DateTime(tz) | |

Unique constraint: `(organization_id, machine_id)`.

**`AgentJob`**

| Field | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `organization_id` | UUID FK | |
| `agent_registration_id` | UUID FK → AgentRegistration | |
| `change_request_id` | UUID FK, nullable | Links to originating CR |
| `command` | String | `estimate_image_size`, `change_ip`, `configure_syslog`, `virtualize_for_migration`, `upload_image` |
| `parameters` | JSON | Command-specific inputs |
| `hmac_signature` | String | Computed at job creation, verified by agent |
| `status` | Enum `pending\|running\|completed\|failed` | |
| `result` | JSON, nullable | Set by agent on completion |
| `error` | Text, nullable | Set on failure |
| `created_at` | DateTime(tz) | |
| `started_at` | DateTime(tz), nullable | Set when agent claims job |
| `completed_at` | DateTime(tz), nullable | Set when agent posts result |

### 2.2 Authentication

All `/agent/...` endpoints validate `Authorization: Bearer {secret}` against the org's stored agent secret in `OrganizationSettings` (same SecretsService used for the Anthropic key). A new field `agent_secret_encrypted` is added to `OrganizationSettings`. The Settings page (admin only) gains an "Agent Secret" section alongside the AI key section.

### 2.3 New Endpoints

**`POST /agent/register`**

Upserts `AgentRegistration` by `(organization_id, machine_id)`. Creates or updates an `Asset`:
- `asset_type`: `server`
- `name`: hostname
- `environment`: `prod` (default, operator can change in UI)
- `criticality`: `medium` (default)
- `tags`: `["nexplane-agent", os_type]`
- `asset_metadata`: `{ hostname, os_version, agent_version, ip_addresses, last_seen }`

Returns: `{ "agent_id": uuid, "asset_id": uuid }`

**`GET /agent/jobs/next?agent_id={uuid}`**

Long-polls up to 30 seconds. Queries for the oldest `pending` job where `agent_registration_id = agent_id`. If found:
- Sets `status = running`, `started_at = now()`
- Returns: `{ "job_id": uuid, "command": str, "parameters": {...}, "hmac_signature": str }`

If nothing found within 30 seconds: returns `204 No Content`.

Also updates `AgentRegistration.last_seen` on each poll.

**`POST /agent/jobs/{job_id}/result`**

Body: `{ "status": "completed"|"failed", "result": {...}, "error": "..." }`

Sets `AgentJob.status`, `result`, `error`, `completed_at`. Returns `200 OK`.

### 2.4 HMAC Signature Computation

At job creation time the backend computes:

```python
import hmac, hashlib, json

def sign_job(secret: str, job_id: str, command: str, parameters: dict) -> str:
    canonical = json.dumps(parameters, sort_keys=True, separators=(",", ":"))
    message = f"{job_id}:{command}:{canonical}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
```

This signature is stored in `AgentJob.hmac_signature` and returned to the agent in the poll response.

### 2.5 New Files (Backend)

| File | Purpose |
|---|---|
| `backend/app/models/agent.py` | `AgentRegistration`, `AgentJob` ORM models, enums |
| `backend/app/schemas/agent.py` | Pydantic schemas for register, poll, result endpoints |
| `backend/app/routers/agent.py` | `/agent/register`, `/agent/jobs/next`, `/agent/jobs/{id}/result` |
| `backend/alembic/versions/006_add_agent_models.py` | Migration: `agent_registrations`, `agent_jobs` tables; `agent_secret_encrypted` on `org_settings`; new enum values |

### 2.6 Modified Files (Backend)

| File | Change |
|---|---|
| `backend/app/main.py` | Register agent router |
| `backend/app/models/org_settings.py` | Add `agent_secret_encrypted` column |
| `backend/app/models/connector.py` | Add `nexplane_agent` to `ConnectorType` enum |
| `backend/app/models/asset.py` | No change — `identity` already added in Spec 3 |
| `backend/app/routers/settings.py` | Add `PUT /settings/agent-secret` endpoint (admin only) |
| `backend/app/schemas/org_settings.py` | Add `AgentSecretUpdate` schema, `agent_configured: bool` to `OrgSettingsRead` |
| `frontend/src/types/api.ts` | Add `nexplane_agent` to `ConnectorType` |
| `frontend/src/pages/Settings.tsx` | Add Agent Secret section alongside AI key section |

---

## Section 3: Connector Catalog

A `nexplane_agent` catalog JSON is added at `backend/app/connectors/catalog/nexplane_agent_mock.json`. For the mock, executors return realistic fake results. The real executor (future) creates `AgentJob` rows and polls until completion.

The catalog defines all five commands as `action_type: "change"` with `execution_tier: 3`:

| action_id | display_name | rollback_action |
|---|---|---|
| `estimate_image_size` | Estimate Image Size & Check Space | — (read-only, no rollback action) |
| `change_ip` | Change IP Address | `change_ip` (rollback flag in parameters) |
| `configure_syslog` | Configure Syslog Forwarding | `configure_syslog` (rollback flag in parameters) |
| `virtualize_for_migration` | Virtualize Machine for Migration | `virtualize_for_migration` (rollback = delete image) |
| `upload_image` | Upload Image to Object Storage | `upload_image` (rollback = delete S3 object) |

Rollback jobs use the same `command` name as the forward job, with a `rollback: true` parameter and the `result` JSON from the completed forward job passed as `previous_result`. The executor routes on the `rollback` flag.

---

## Section 4: Command Specifications

### 4.1 `estimate_image_size` (read-only preflight)

**Parameters:**
- `source_device` (string, optional) — disk to image. Auto-detected from root mount if omitted.
- `destination_path` (string, required) — local path where the image will be written.

**Execute:**
- Determines source disk size in bytes.
  - Linux: `lsblk -b -d -o SIZE {device}` or parse `/proc/partitions`.
  - Windows: `Get-Disk | Where-Object { $_.IsSystem } | Select-Object -ExpandProperty Size`.
- Determines available space at `destination_path`.
  - Linux: `statvfs(destination_path)` → `f_bavail * f_frsize`.
  - Windows: `fsutil volume diskfree {drive}` or `Get-PSDrive`.
- `recommended_minimum_bytes = source_size_bytes * 1.1` (10% headroom).
- `sufficient_space = destination_available_bytes >= recommended_minimum_bytes`.

**Result:**
```json
{
  "source_device": "/dev/sda",
  "source_size_bytes": 107374182400,
  "destination_path": "/mnt/images",
  "destination_available_bytes": 214748364800,
  "recommended_minimum_bytes": 118111600640,
  "sufficient_space": true
}
```

**Rollback:** None — read-only.

---

### 4.2 `change_ip`

**Parameters:**
- `interface` (string, required) — interface name e.g. `eth0`, `Ethernet`.
- `mode` (string, required) — `static` or `dhcp`.
- `ip_version` (string, default `4`) — `4`, `6`, or `both`.
- `new_ip_v4` (CIDR string, e.g. `10.0.1.50/24`) — required if `mode=static` and `ip_version=4|both`.
- `new_ip_v6` (CIDR string, e.g. `2001:db8::1/64`) — required if `mode=static` and `ip_version=6|both`.
- `new_gateway_v4` (string, optional).
- `new_gateway_v6` (string, optional).
- `dns_servers` (list of strings, optional).

**Execute snapshot** — captured before any change:
```json
{
  "interface": "eth0",
  "ipv4": { "mode": "static", "address": "10.0.1.10/24", "gateway": "10.0.1.1" },
  "ipv6": { "mode": "dhcp", "address": null, "gateway": null },
  "dns_servers": ["8.8.8.8"]
}
```

**Linux execution** (detection order):
1. NetworkManager present (`nmcli` available): use `nmcli con mod` to set `ipv4.method auto|manual`, `ipv4.addresses`, `ipv4.gateway`, `ipv6.method auto|manual`, `ipv6.addresses`, `ipv6.gateway`, `ipv4.dns`. Then `nmcli con up {connection}`.
2. systemd-networkd: write a `.network` file in `/etc/systemd/network/` and `networkctl reload`.
3. Debian/Ubuntu legacy: edit `/etc/network/interfaces`, run `ifdown {iface} && ifup {iface}`.
4. RHEL/CentOS legacy: edit `/etc/sysconfig/network-scripts/ifcfg-{iface}`, run `ifdown {iface} && ifup {iface}`.

**Windows execution:**
- IPv4 static: `netsh interface ipv4 set address name="{iface}" static {ip} {mask} {gw}`
- IPv4 DHCP: `netsh interface ipv4 set address name="{iface}" dhcp`
- IPv6 static: `netsh interface ipv6 set address "{iface}" {ipv6_cidr}` + `netsh interface ipv6 set route ::/0 "{iface}" {gw}`
- IPv6 DHCP: `netsh interface ipv6 set address "{iface}" dhcp` (or enable via interface config)
- DNS: `netsh interface ipv4 set dns name="{iface}" static {dns1}` + add additional servers.

**Rollback:** Re-apply snapshot values using the same detection path.

---

### 4.3 `configure_syslog`

**Parameters:**
- `destination_host` (string, required) — log collector hostname or IP.
- `destination_port` (int, required) — e.g. `514`.
- `protocol` (string, required) — `udp` or `tcp`.
- `facility` (string, optional, default `*`) — syslog facility filter e.g. `auth`, `kern`, `*`.

**Execute snapshot** — captures existing syslog config file contents (or registry value on Windows) before modification.

**Linux execution** (detection order):
1. rsyslog (`/etc/rsyslog.conf` or `/etc/rsyslog.d/` exists): append a delimited forwarding block:
   ```
   # nexplane-managed-begin
   {facility}.*  @{host}:{port}    # UDP
   {facility}.*  @@{host}:{port}   # TCP
   # nexplane-managed-end
   ```
   Then `systemctl restart rsyslog`.
2. syslog-ng (`/etc/syslog-ng/syslog-ng.conf` exists): append a `destination` + `log` block within the nexplane-managed delimiters. Then `systemctl restart syslog-ng`.

**Windows execution** (detection order):
1. NXLog (`C:\Program Files\nxlog\` exists): append a forwarding output block to `nxlog.conf` within nexplane-managed delimiters. Restart NXLog service.
2. No syslog agent: configure Windows Event Forwarding (WEF) via `wecutil cs` to push events to a WEF collector at `destination_host:destination_port`.

**Rollback:** Remove the nexplane-managed delimited block (Linux) or delete the WEF subscription (Windows). Restore from snapshot. Restart the affected service.

---

### 4.4 `virtualize_for_migration`

**Parameters:**
- `image_path` (string, required) — local path to write the disk image.
- `target_mode` (string, required) — `static` or `dhcp`.
- `target_ip_v4` (CIDR, optional) — required if `target_mode=static` and IPv4 needed.
- `target_ip_v6` (CIDR, optional) — required if `target_mode=static` and IPv6 needed.
- `target_gateway_v4` (string, optional).
- `target_gateway_v6` (string, optional).
- `target_dns_servers` (list of strings, optional).
- `target_interface` (string, optional) — auto-detected from primary interface if omitted.
- `source_device` (string, optional) — auto-detected from root mount if omitted.

**Linux execution:**
1. Detect source block device from `/proc/mounts` (root mount → device).
2. `dd if={source_device} of={image_path} bs=4M status=progress conv=fsync` — raw disk image.
3. `losetup --find --show --partscan {image_path}` → loop device e.g. `/dev/loop0`.
4. Identify root partition inside loop device (`lsblk`, check for `/` in partition label or try mounting each).
5. Mount root partition to a temp dir (`/tmp/nexplane-mount-{uuid}`).
6. Apply `change_ip` parameters to the network config inside the mounted filesystem — uses same detection logic but with `root={mount_point}` prefix on all paths.
7. Unmount: `umount {mount_point}`.
8. Detach: `losetup -d {loop_device}`.

**Windows execution:**
1. Get source disk size: `Get-Disk -Number 0 | Select-Object -ExpandProperty Size`.
2. Create fixed VHD: `New-VHD -Path {image_path} -SizeBytes {size} -Fixed`.
3. Mount VHD: `Mount-DiskImage -ImagePath {image_path}`.
4. Identify mounted drive letter.
5. Copy system files: `robocopy C:\ {drive}:\ /E /MIR /XD "$RECYCLE.BIN" "System Volume Information"` (or use DISM `/Capture-Image` for WIM).
6. Edit network config in the mounted volume — reads/writes registry hive (`ntuser.dat`, system hive) or edits `unattend.xml` if present.
7. Dismount: `Dismount-DiskImage -ImagePath {image_path}`.

**Result:**
```json
{
  "image_path": "/mnt/images/server01.img",
  "image_size_bytes": 107374182400,
  "source_device": "/dev/sda",
  "network_config_applied": true
}
```

**Rollback:** Delete `image_path`. The source machine is entirely untouched.

---

### 4.5 `upload_image`

**Parameters:**
- `image_path` (string, required) — local path to the image file.
- `destination_uri` (string, required) — object storage URI. Currently supported scheme: `s3://bucket/key`. Future: `az://container/blob`, `gs://bucket/object`.
- `region` (string, optional) — AWS region. Defaults to `AWS_DEFAULT_REGION` env var or instance metadata.
- `access_key_id` (string, optional) — explicit AWS credential.
- `secret_access_key` (string, optional) — explicit AWS credential.

**Credential resolution order (AWS SDK standard chain):**
1. Explicit parameters (`access_key_id`, `secret_access_key`).
2. Environment variables (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`).
3. Shared credentials file (`~/.aws/credentials`).
4. EC2 instance IAM role metadata (`http://169.254.169.254/...`).

**Execute:**
- Parse `destination_uri` to extract bucket and key.
- Use AWS SDK for Go v2 `s3manager.Upload` (handles multipart transparently for large files).
- Verify upload: `HeadObject` to confirm ETag and size match local file SHA256/MD5.
- Returns: `{ "destination_uri": "s3://...", "etag": "...", "size_bytes": N, "checksum_verified": true }`.

**Rollback:** `s3.DeleteObject` using `destination_uri` from the job result.

---

## Section 5: Full Virtualization Workflow

The intended project in Nexplane for migrating a physical machine to EC2:

```
CR 1: estimate_image_size        (nexplane_agent)
      → Confirms space available. Blocks all subsequent CRs if failed.

CR 2: virtualize_for_migration   (nexplane_agent)
      → Creates raw disk image at image_path with target IP pre-configured.
      → Depends on: CR 1

CR 3: upload_image               (nexplane_agent)
      → Uploads image to s3://bucket/key.
      → Depends on: CR 2

CR 4: register AMI / launch EC2  (aws connector — existing or future)
      → Imports snapshot, registers AMI, launches instance.
      → Depends on: CR 3
```

CRs 1–3 use the `nexplane_agent` connector. CR 4 uses the AWS API connector. The agent never needs EC2 launch permissions — only S3 upload credentials for CR 3.

---

## Section 6: Build & Distribution

The agent is compiled via `goreleaser` or a simple `Makefile`:

```makefile
build-linux:
    GOOS=linux GOARCH=amd64 go build -o dist/nexplane-agent-linux-amd64 ./

build-linux-arm:
    GOOS=linux GOARCH=arm64 go build -o dist/nexplane-agent-linux-arm64 ./

build-windows:
    GOOS=windows GOARCH=amd64 go build -o dist/nexplane-agent-windows-amd64.exe ./
```

Distribution (future): binaries served from the Nexplane Settings page as downloadable artifacts with the org's agent secret pre-filled in a copy-paste install command.

---

## Section 7: Example Invocations

**Ephemeral (via SSH, run once):**
```bash
./nexplane-agent \
  --control-plane https://nexplane.acme.example:8000 \
  --secret sk-agent-abc123 \
  --mode ephemeral
```

**Service (systemd):**
```ini
[Unit]
Description=Nexplane Agent
After=network.target

[Service]
ExecStart=/usr/local/bin/nexplane-agent \
  --control-plane https://nexplane.acme.example:8000 \
  --secret sk-agent-abc123 \
  --mode service \
  --poll-interval 30s
Restart=on-failure
RestartSec=10s
Environment=NP_CONTROL_PLANE=https://nexplane.acme.example:8000

[Install]
WantedBy=multi-user.target
```

**Windows Service (PowerShell):**
```powershell
New-Service -Name "NexplaneAgent" `
  -BinaryPathName "C:\nexplane\nexplane-agent.exe --mode service --poll-interval 30s" `
  -StartupType Automatic
Start-Service NexplaneAgent
```
