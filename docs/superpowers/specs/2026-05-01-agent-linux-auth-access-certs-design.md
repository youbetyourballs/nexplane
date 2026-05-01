# Agent: Linux Auth, Access & Certificates — Design Spec (Spec 5b)

**Date:** 2026-05-01
**Status:** Approved
**Scope:** New agent commands for Linux authentication hardening, access control, certificate management, and time synchronization — PAM hardening, SSH hardening, user/group audit (ingest), privilege escalation vulnerability assessment (ingest), CA certificate management, NTP/chrony configuration. All commands are Linux-only. All become catalog actions in `nexplane_agent_mock.json`.

---

## Design Decisions

- **Privilege model:** Agent must run as root (requirement A). Each command runs an upfront capability preflight before touching system state.
- **Rollback contract:** Every `Execute` that modifies system state captures a snapshot before changes, stores it in the result under `"snapshot"`, and implements `Rollback` that reads `snapshot` from merged params and restores prior state.
- **Drop-in file pattern:** Where supported (SSH), use drop-in config files (`/etc/ssh/sshd_config.d/99-nexplane-hardening.conf`) rather than editing the main config. This produces a clean rollback (delete the file) and avoids conflicts with other management tools.
- **Nexplane-managed blocks:** Where drop-in files are not supported (PAM, NTP), changes are written within clearly delimited `# nexplane-managed-begin` / `# nexplane-managed-end` blocks so rollback can surgically remove them.
- **Distro detection:** PAM, CA certificates, and NTP commands detect the Linux distro variant at runtime and choose the appropriate tool/path (`pam_pwquality` vs `pam_cracklib`; `update-ca-certificates` vs `update-ca-trust`; `chronyd` vs `systemd-timesyncd` vs `ntpd`).
- **Read-only ingest commands:** `audit_users_and_groups` and `audit_privesc_vulnerabilities` are read-only. They enrich asset metadata and tag assets with findings. No rollback needed.
- **Catalog integration:** All 6 commands get `nexplane_agent_mock.json` catalog entries and mock executor stubs so they are selectable in projects and visible to the AI planning assistant.

---

## Section 1: Authentication and Access Hardening

### 1.1 `configure_pam`

**Parameters:**
- `profile` (string, default `cis_level1`) — `cis_level1`, `cis_level2`, or `custom`
- `params` (map, optional) — specific settings; used to override profile defaults or supply all values when `profile=custom`:
  - `min_password_length` (int, default 14)
  - `password_complexity` (string `enabled`|`disabled`, default `enabled`) — controls `pam_pwquality` or `pam_cracklib`
  - `max_failed_attempts` (int, default 5) — configures `pam_faillock` or `pam_tally2`
  - `lockout_duration_seconds` (int, default 900) — 15-minute lockout
  - `remember_passwords` (int, default 5) — prevents reuse via `pam_pwhistory`
  - `session_timeout_seconds` (int, optional) — idle session termination via `TMOUT` in `/etc/profile.d/`

**Preflight:**
- Checks caller is root
- Detects PAM variant: checks for `pam_pwquality.so` (modern, RHEL 7+/Ubuntu 18+) vs `pam_cracklib.so` (legacy); `pam_faillock.so` (modern) vs `pam_tally2.so` (legacy)
- Checks `/etc/pam.d/` exists and is writable

**Execute:**
1. Snapshot: copy all modified files — `/etc/security/pwquality.conf`, `/etc/security/faillock.conf` (or `/etc/security/pam_tally2.conf`), `/etc/pam.d/common-auth`, `/etc/pam.d/common-password`, `/etc/pam.d/common-account` (or RHEL equivalents `/etc/pam.d/system-auth`, `/etc/pam.d/password-auth`)
2. Write `/etc/security/pwquality.conf` (or equivalent) with `minlen`, `dcredit`, `ucredit`, `ocredit`, `lcredit` values derived from profile
3. Write `/etc/security/faillock.conf` with `deny = {max_failed_attempts}`, `unlock_time = {lockout_duration_seconds}`
4. Insert/update PAM module references in the relevant `/etc/pam.d/` files within nexplane-managed blocks:
   - `common-password` / `password-auth`: add `pam_pwquality.so` + `pam_pwhistory.so remember={n}`
   - `common-auth` / `system-auth`: add `pam_faillock.so preauth` (before `pam_unix`) and `pam_faillock.so authfail` (after)
   - `common-account` / `account`: add `pam_faillock.so`
5. If `session_timeout_seconds` supplied: write `TMOUT={n}; readonly TMOUT; export TMOUT` to `/etc/profile.d/99-nexplane-timeout.sh`

**Result:** `{ profile_applied, pam_variant, params_applied, files_snapshot }`

**Rollback:** Restore all files from `files_snapshot`; remove `/etc/profile.d/99-nexplane-timeout.sh` if it was written.

**Catalog entry:**
```json
{
  "action_id": "configure_pam",
  "action_type": "change",
  "execution_tier": 3,
  "applicable_asset_types": ["server"],
  "rollback_action": "configure_pam",
  "safety_notes": [
    "Misconfigured PAM can lock out all users including root — test in a non-production environment first",
    "lockout_duration_seconds applies to all accounts; ensure break-glass access exists before applying"
  ]
}
```

---

### 1.2 `harden_ssh`

**Parameters:**
- `settings` (map, optional) — sshd_config key→value pairs, merged with secure defaults. All keys are optional; defaults are applied for any not specified:

| Setting | Default | Notes |
|---------|---------|-------|
| `permit_root_login` | `no` | |
| `password_authentication` | `no` | Requires key-based auth to be in place |
| `pubkey_authentication` | `yes` | |
| `allowed_ciphers` | `chacha20-poly1305@openssh.com,aes256-gcm@openssh.com,aes128-gcm@openssh.com` | |
| `allowed_macs` | `hmac-sha2-512-etm@openssh.com,hmac-sha2-256-etm@openssh.com` | |
| `allowed_kex_algorithms` | `curve25519-sha256,curve25519-sha256@libssh.org,diffie-hellman-group16-sha512` | |
| `client_alive_interval` | `300` | Seconds between keepalive probes |
| `client_alive_count_max` | `3` | Max missed keepalives before disconnect |
| `max_auth_tries` | `4` | |
| `login_grace_time` | `60` | Seconds to authenticate |
| `x11_forwarding` | `no` | |
| `permit_empty_passwords` | `no` | |
| `port` | (unchanged) | Omit to keep existing port |
| `allow_users` | (none) | Space-separated list if supplied |
| `allow_groups` | (none) | Space-separated list if supplied |

**Preflight:**
- Checks caller is root
- Checks `sshd` is installed and service is running
- Checks `/etc/ssh/sshd_config.d/` directory exists (creates it if OpenSSH ≥ 7.3; falls back to editing main config for older versions)
- Verifies key-based auth is configured before disabling password auth (checks for authorized_keys for at least one non-root user)

**Execute:**
1. Snapshot: copy `/etc/ssh/sshd_config` and all files in `/etc/ssh/sshd_config.d/`
2. Write `/etc/ssh/sshd_config.d/99-nexplane-hardening.conf` with all settings in standard sshd_config format
3. Validate: `sshd -t` — if validation fails, remove the drop-in file and return error (no service disruption)
4. `systemctl reload sshd` (reload preserves existing sessions; does not restart)

**Result:** `{ settings_applied, drop_in_path, sshd_config_snapshot }`

**Rollback:**
- Remove `/etc/ssh/sshd_config.d/99-nexplane-hardening.conf` (or restore main config from snapshot for older OpenSSH)
- `systemctl reload sshd`

**Catalog entry:**
```json
{
  "action_id": "harden_ssh",
  "action_type": "change",
  "execution_tier": 3,
  "applicable_asset_types": ["server"],
  "rollback_action": "harden_ssh",
  "safety_notes": [
    "Disabling password_authentication requires key-based auth to be working — verify before applying",
    "Changing the SSH port requires firewall rules to be updated before reloading"
  ]
}
```

---

## Section 2: Audit Ingest Commands

### 2.1 `audit_users_and_groups` (read-only ingest)

No parameters required.

**Preflight:** Checks caller is root (required to read `/etc/shadow`).

**Checks performed:**

| Check | Method | Finding tag |
|-------|--------|-------------|
| Accounts with no password expiry | `chage -l` for each account | `user-no-expiry` |
| Accounts with UID 0 other than root | Parse `/etc/passwd` | `uid0-non-root` |
| Service accounts with interactive shells | `/etc/passwd` — non-system UIDs with `/bin/bash` or `/bin/sh` | `svc-interactive-shell` |
| Accounts with empty or locked passwords | Parse `/etc/shadow` second field | `empty-password` |
| Sudo group members beyond expected principals | Parse `/etc/group` for `sudo`/`wheel` group | `unexpected-sudo-member` |
| World-writable home directories | `stat` on each home directory | `world-writable-home` |

**Result:** Structured list of findings per account. Enriches asset `asset_metadata` with `users_audit` key. Adds `users-audit-findings` tag to asset if any findings present.

No rollback — read-only.

**Catalog entry:**
```json
{
  "action_id": "audit_users_and_groups",
  "action_type": "change",
  "execution_tier": 3,
  "applicable_asset_types": ["server", "identity"]
}
```

---

### 2.2 `audit_privesc_vulnerabilities` (read-only ingest)

No parameters required.

**Preflight:** Checks caller is root (required to accurately enumerate SUID binaries, check service files, and run `sudo -l`).

**Checks performed:**

| Check | Method | CVE / Tag |
|-------|--------|-----------|
| PwnKit | Check `polkit` version installed | CVE-2021-4034 |
| DirtyPipe | Kernel version < 5.16.11/5.15.25/5.10.102 | CVE-2022-0847 |
| Dirty COW | Kernel version < 4.8.3 | CVE-2016-5195 |
| HiveNightmare | N/A (Linux) | — |
| Unexpected SUID/SGID binaries | `find / -perm -4000 -o -perm -2000 2>/dev/null` against known-good baseline | `unexpected-suid` |
| World-writable cron directories | `stat /etc/cron.*` | `writable-cron` |
| World-writable service unit files | `find /etc/systemd /lib/systemd -perm -o+w 2>/dev/null` | `writable-service` |
| Misconfigured sudo rules | `sudo -l -U {user}` for each account with sudo access | `sudo-misconfigured` |
| Writable PATH directories owned by non-root | Check `$PATH` entries for world/group write | `writable-path` |
| copy_file_range variant | Kernel version check against published ranges | CVE family |

**Result:** Per-finding: `{ cve_id, severity, condition_present, description, remediation }`. Enriches asset metadata with `privesc_findings` key. Adds `privesc-risk:critical`, `privesc-risk:high`, or `privesc-risk:medium` tag based on highest severity finding.

No rollback — read-only.

**Catalog entry:**
```json
{
  "action_id": "audit_privesc_vulnerabilities",
  "action_type": "change",
  "execution_tier": 3,
  "applicable_asset_types": ["server"]
}
```

---

## Section 3: Certificates and Time

### 3.1 `manage_ca_certificates`

**Parameters:**
- `action` (string, required) — `install` or `remove`
- `certificate` (string, required) — PEM-encoded CA certificate content
- `cert_name` (string, required) — filename stem, used as `{cert_name}.crt`

**Distro detection:**
- Debian/Ubuntu: install to `/usr/local/share/ca-certificates/{cert_name}.crt`; run `update-ca-certificates`
- RHEL/CentOS/Fedora: install to `/etc/pki/ca-trust/source/anchors/{cert_name}.crt`; run `update-ca-trust extract`

**Preflight:**
- Checks caller is root
- Validates PEM is a valid X.509 certificate: `openssl x509 -noout -in {tmpfile}` (write PEM to temp, validate, delete temp)
- For `remove`: checks the named certificate file exists

**Execute:**
1. Snapshot: copy the existing cert file at the target path if present (empty string if absent)
2. For `install`: write PEM to the distro-appropriate path; run trust store update command
3. For `remove`: delete the cert file; run trust store update command

**Result:** `{ action, cert_path, cert_name, cert_subject, cert_expiry, snapshot }`

**Rollback:**
- For `install`: delete cert file + re-run trust store update
- For `remove`: write `snapshot` content back to cert path + re-run trust store update

**Catalog entry:**
```json
{
  "action_id": "manage_ca_certificates",
  "action_type": "change",
  "execution_tier": 3,
  "applicable_asset_types": ["server"],
  "rollback_action": "manage_ca_certificates",
  "safety_notes": [
    "Installing an untrusted CA certificate compromises TLS security for all processes on this host"
  ]
}
```

---

### 3.2 `configure_ntp`

**Parameters:**
- `servers` (list of strings, required) — NTP server addresses (e.g. `["time.cloudflare.com", "pool.ntp.org"]`)
- `require_iburst` (bool, default `true`) — add `iburst` option to each server directive
- `makestep` (string, default `1.0 3`) — chrony makestep directive (step threshold and limit)

**NTP daemon detection order:**
1. chrony: `/etc/chrony.conf` or `/etc/chrony/chrony.conf` exists → service `chronyd`
2. systemd-timesyncd: `/etc/systemd/timesyncd.conf` exists → service `systemd-timesyncd`
3. ntpd: `/etc/ntp.conf` exists → service `ntp` or `ntpd`

**Preflight:**
- Checks caller is root
- Checks at least one NTP daemon is installed and running

**Execute:**
1. Snapshot: copy the detected config file
2. Remove existing `server` and `pool` lines from config within a nexplane-managed block; insert new server directives with `iburst` if requested
3. For chrony: also write `makestep {makestep}` and `rtcsync` if not present
4. Restart the NTP service

**Result:** `{ daemon, config_path, servers_configured, snapshot }`

**Rollback:** Restore config from snapshot; restart NTP service.

**Catalog entry:**
```json
{
  "action_id": "configure_ntp",
  "action_type": "change",
  "execution_tier": 3,
  "applicable_asset_types": ["server"],
  "rollback_action": "configure_ntp",
  "safety_notes": [
    "NTP misconfiguration causes clock drift which invalidates audit log timestamps and TLS certificate validation"
  ]
}
```

---

## Section 4: New Files

| File | Purpose |
|------|---------|
| `agent/commands/linuxauth/linuxauth.go` | Package entry: `Execute`/`Rollback` dispatch |
| `agent/commands/linuxauth/pam_linux.go` | `configure_pam` implementation |
| `agent/commands/linuxauth/ssh_linux.go` | `harden_ssh` implementation |
| `agent/commands/linuxauth/users_linux.go` | `audit_users_and_groups` ingest |
| `agent/commands/linuxauth/privesc_linux.go` | `audit_privesc_vulnerabilities` ingest |
| `agent/commands/linuxauth/certs_linux.go` | `manage_ca_certificates` implementation |
| `agent/commands/linuxauth/ntp_linux.go` | `configure_ntp` implementation |
| `agent/commands/linuxauth/linuxauth_test.go` | Validation tests (no build tag — param validation is cross-platform) |

## Section 5: Modified Files

| File | Change |
|------|--------|
| `agent/executor/executor.go` | Add 6 new commands to `commands` and `rollbacks` maps |
| `backend/app/connectors/catalog/nexplane_agent_mock.json` | Add 6 catalog entries |
| `backend/app/connectors/executors/nexplane_agent_mock/` | Add 6 mock executor stubs |
