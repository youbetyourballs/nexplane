# Vulnerability Mitigation CR Types — Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add five missing mitigation CR types to the vulnerability pipeline so the `/findings/{id}/mitigate` endpoint can dispatch any mitigation tactic the platform supports — not just network isolation and WAF rules.

**Architecture:** Each CR type follows the existing pattern: `ChangeType` enum entry → JSON definition in `change_type_definitions/` → executor in `connectors/executors/` → wired into the `mitigate` endpoint's `mitigation_type` dispatch map. No new models, no new routers.

**Tech Stack:** FastAPI, SQLAlchemy, SSH/Agent/AWS connectors (existing), pytest

---

## The Five Missing CR Types

### 1. `apply_protocol_control`

Disable a vulnerable network protocol on a Linux or Windows host (TLS 1.0, SSLv3, SMBv1, NTLMv1).

**Connector:** SSH (Linux) or Agent (Linux/Windows)

**Parameters:**
- `protocol`: `"tls10"` | `"tls11"` | `"sslv3"` | `"smbv1"` | `"ntlmv1"`
- `target_os`: `"linux"` | `"windows"`

**Execution (Linux SSH):**
```bash
# TLS 1.0 disable via OpenSSL config
grep -q "MinProtocol" /etc/ssl/openssl.cnf && \
  sed -i 's/MinProtocol.*/MinProtocol = TLSv1.2/' /etc/ssl/openssl.cnf || \
  echo "MinProtocol = TLSv1.2" >> /etc/ssl/openssl.cnf
```

**Rollback:** Restore original config line via pre-captured `pre_state` (the original value read before applying).

**Risk score:** 5 (medium — protocol change can break legacy clients)

---

### 2. `disable_kernel_feature`

Disable a vulnerable kernel module or feature on Linux.

**Connector:** SSH or Agent (Linux only)

**Parameters:**
- `feature`: `"ipv6"` | `"usb_storage"` | `"cramfs"` | `"freevxfs"` | `"jffs2"` | `"hfs"` | `"hfsplus"` | `"squashfs"` | `"udf"` | module name string

**Execution:**
```bash
# Write to modprobe blacklist
echo "blacklist ${feature}" >> /etc/modprobe.d/nexplane-disable.conf
# Unload if currently loaded
rmmod "${feature}" 2>/dev/null || true
```

**Rollback:**
```bash
# Remove the added line
sed -i "/^blacklist ${feature}$/d" /etc/modprobe.d/nexplane-disable.conf
modprobe "${feature}" 2>/dev/null || true
```

**Risk score:** 4 (low-medium — module blacklist; needs reboot for full effect)

---

### 3. `apply_registry_fix`

Set a Windows registry key to mitigate a CVE (e.g., DisableHttp2, ScreenSaverIsSecure, DisableRemoteDesktop).

**Connector:** Agent (Windows only) or WinRM

**Parameters:**
- `key_path`: full registry path (e.g., `HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services`)
- `value_name`: registry value name
- `value_data`: string or DWORD
- `value_type`: `"String"` | `"DWORD"` | `"ExpandString"`

**Execution (PowerShell via Agent):**
```powershell
$existing = Get-ItemProperty -Path "$key_path" -Name "$value_name" -ErrorAction SilentlyContinue
New-ItemProperty -Path "$key_path" -Name "$value_name" -Value "$value_data" -PropertyType "$value_type" -Force
```

**Pre-state capture:** Read current value before writing. Store in `pre_state` for rollback.

**Rollback:** Restore original value if it existed; remove the key if it was newly created.

**Risk score:** 5 (medium — registry changes can affect system behavior)

---

### 4. `remove_vulnerable_package`

Remove a package that has no available patch and poses unacceptable risk.

**Connector:** SSH or Agent (Linux) or WinRM (Windows)

**Parameters:**
- `package_name`: e.g., `"telnet"`, `"xinetd"`, `"rsh-server"`
- `target_os`: `"linux"` | `"windows"`

**Execution (Linux):**
```bash
# Capture installed version for rollback
INSTALLED=$(rpm -q "${package_name}" 2>/dev/null || dpkg -s "${package_name}" 2>/dev/null | grep Version || echo "")
# Remove
apt-get remove -y "${package_name}" 2>/dev/null || yum remove -y "${package_name}" 2>/dev/null || dnf remove -y "${package_name}" 2>/dev/null
```

**Pre-state:** Store `INSTALLED` version string.

**Rollback:** Reinstall pinned version from pre-state. If package no longer available in repo, log warning — operator must intervene.

**Risk score:** 6 (medium-high — removal is hard to reverse if repo version has changed)

---

### 5. `revoke_exposed_credential`

Immediately revoke a known-compromised credential. Routes to the appropriate connector based on `credential_type`.

**Connectors:** AWS (IAM key), GCP (service account key), Azure (client secret), LDAP/Vault/Infisical (generic secret)

**Parameters:**
- `credential_type`: `"aws_iam_key"` | `"gcp_service_account_key"` | `"azure_client_secret"` | `"vault_token"` | `"ldap_password"`
- `credential_id`: the key ID, token, or account name
- `connector_id`: UUID of the connector to use

**Execution:** Immediate deletion/revocation — does not wait for a replacement. This is emergency response, not rotation.

**Rollback:** Not available — revocation is permanent. This is surfaced in the UI as a warning before execution. Risk level is forced to `critical`, requires 2 approvers.

**Risk score:** 9 (critical — permanent; no rollback)

---

## Wiring to the Mitigate Endpoint

`POST /vulnerability/findings/{id}/mitigate` currently dispatches on `mitigation_type`:

```python
MITIGATION_CR_MAP = {
    "network_isolation": ChangeType.security_group_update,
    "waf": ChangeType.microsegmentation_policy,
    "feature_flag": ChangeType.ssm_command,
    "rate_limit": ChangeType.ssm_command,
    "virtual_patch": ChangeType.ssm_command,
    "seccomp": ChangeType.agent_ossecurity,
    "apparmor": ChangeType.agent_ossecurity,
    "ebpf": ChangeType.agent_ossecurity,
    "capability_drop": ChangeType.agent_ossecurity,
    # NEW:
    "protocol_control": ChangeType.apply_protocol_control,
    "kernel_feature": ChangeType.disable_kernel_feature,
    "registry_fix": ChangeType.apply_registry_fix,
    "package_remove": ChangeType.remove_vulnerable_package,
    "credential_revoke": ChangeType.revoke_exposed_credential,
}
```

Each new `mitigation_type` maps to its new CR type. The caller passes `mitigation_parameters` that become the CR's `desired_outcome`.

---

## Files to Create/Modify

| File | Action |
|------|--------|
| `backend/app/models/change_request.py` | Add 5 new `ChangeType` enum values |
| `backend/alembic/versions/0XX_add_mitigation_cr_types.py` | Migration adding enum values |
| `backend/app/connectors/executors/ssh/apply_protocol_control.py` | Executor (Linux SSH/Agent) |
| `backend/app/connectors/executors/ssh/disable_kernel_feature.py` | Executor (Linux SSH/Agent) |
| `backend/app/connectors/executors/winrm/apply_registry_fix.py` | Executor (Windows Agent/WinRM) |
| `backend/app/connectors/executors/ssh/remove_vulnerable_package.py` | Executor (Linux/Windows) |
| `backend/app/connectors/executors/aws/revoke_exposed_credential.py` | Executor (multi-connector) |
| `backend/app/connectors/change_type_definitions/apply_protocol_control.json` | CT definition |
| `backend/app/connectors/change_type_definitions/disable_kernel_feature.json` | CT definition |
| `backend/app/connectors/change_type_definitions/apply_registry_fix.json` | CT definition |
| `backend/app/connectors/change_type_definitions/remove_vulnerable_package.json` | CT definition |
| `backend/app/connectors/change_type_definitions/revoke_exposed_credential.json` | CT definition |
| `backend/app/routers/vulnerability.py` | Add 5 entries to `MITIGATION_CR_MAP` |
| `backend/tests/test_vuln_mitigation_cr_types.py` | Unit tests |

---

## Testing Strategy

Unit tests cover: enum contains new values, MITIGATION_CR_MAP maps correctly, executor `build_steps()` returns expected steps for known inputs, rollback steps are populated correctly. No live connector calls in unit tests — executors tested with mocked connector clients.

Smoke: extend VULN_REMEDIATION smoke phase to call `POST /findings/{id}/mitigate` with `protocol_control` and `kernel_feature` types and verify a CR is created in draft state.
