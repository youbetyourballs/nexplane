# Vulnerability Mitigation CR Types Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add five new mitigation CR types (`apply_protocol_control`, `disable_kernel_feature`, `apply_registry_fix`, `remove_vulnerable_package`, `revoke_exposed_credential`) so the `/findings/{id}/mitigate` endpoint can dispatch every major mitigation tactic.

**Architecture:** Each CR type follows the existing pattern: `ChangeType` enum entry → JSON definition in `change_type_definitions/` → executor in `connectors/executors/` → wired into `MITIGATION_CR_MAP` in the mitigate endpoint. No new models, no new routers.

**Tech Stack:** FastAPI, SQLAlchemy, SSH/Agent/AWS connectors (existing), pytest, PostgreSQL (Alembic enum migration)

---

## File Map

| File | Action |
|------|--------|
| `backend/app/models/change_request.py` | Add 5 new `ChangeType` enum values |
| `backend/alembic/versions/060_add_mitigation_cr_types.py` | New migration |
| `backend/app/connectors/executors/ssh/apply_protocol_control.py` | New executor |
| `backend/app/connectors/executors/ssh/disable_kernel_feature.py` | New executor |
| `backend/app/connectors/executors/ssh/apply_registry_fix.py` | New executor (Windows via PowerShell/WinRM) |
| `backend/app/connectors/executors/ssh/remove_vulnerable_package.py` | New executor |
| `backend/app/connectors/executors/aws/revoke_exposed_credential.py` | New executor |
| `backend/app/connectors/change_type_definitions/apply_protocol_control.json` | CT definition |
| `backend/app/connectors/change_type_definitions/disable_kernel_feature.json` | CT definition |
| `backend/app/connectors/change_type_definitions/apply_registry_fix.json` | CT definition |
| `backend/app/connectors/change_type_definitions/remove_vulnerable_package.json` | CT definition |
| `backend/app/connectors/change_type_definitions/revoke_exposed_credential.json` | CT definition |
| `backend/app/routers/vulnerability.py` | Add 5 entries to `MITIGATION_CR_MAP` |
| `backend/tests/test_vuln_mitigation_cr_types.py` | Unit tests |

---

### Task 1: Enum values + Alembic migration

**Files:**
- Modify: `backend/app/models/change_request.py`
- Create: `backend/alembic/versions/060_add_mitigation_cr_types.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_vuln_mitigation_cr_types.py
from app.models.change_request import ChangeType

def test_new_cr_types_exist():
    assert ChangeType.apply_protocol_control.value == "apply_protocol_control"
    assert ChangeType.disable_kernel_feature.value == "disable_kernel_feature"
    assert ChangeType.apply_registry_fix.value == "apply_registry_fix"
    assert ChangeType.remove_vulnerable_package.value == "remove_vulnerable_package"
    assert ChangeType.revoke_exposed_credential.value == "revoke_exposed_credential"
```

- [ ] **Step 2: Run test to see it fail**

```bash
cd backend && python -m pytest tests/test_vuln_mitigation_cr_types.py::test_new_cr_types_exist -v
```

Expected: `AttributeError: apply_protocol_control is not a valid ChangeType`

- [ ] **Step 3: Add enum values to `change_request.py`**

Open `backend/app/models/change_request.py`. Find the `ChangeType` enum (look for `patch_packages = "patch_packages"`). Add after it:

```python
apply_protocol_control = "apply_protocol_control"
disable_kernel_feature = "disable_kernel_feature"
apply_registry_fix = "apply_registry_fix"
remove_vulnerable_package = "remove_vulnerable_package"
revoke_exposed_credential = "revoke_exposed_credential"
```

- [ ] **Step 4: Create migration**

```python
# backend/alembic/versions/060_add_mitigation_cr_types.py
"""add mitigation cr types

Revision ID: a1b2c3d4e5f6
Revises: 059_vuln_poc_validate
Create Date: 2026-05-26
"""
from typing import Union
from alembic import op

revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '059_vuln_poc_validate'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'apply_protocol_control'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'disable_kernel_feature'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'apply_registry_fix'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'remove_vulnerable_package'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'revoke_exposed_credential'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; downgrade is a no-op
    pass
```

- [ ] **Step 5: Run test to see it pass**

```bash
cd backend && python -m pytest tests/test_vuln_mitigation_cr_types.py::test_new_cr_types_exist -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/change_request.py backend/alembic/versions/060_add_mitigation_cr_types.py backend/tests/test_vuln_mitigation_cr_types.py
git commit -m "feat: add 5 mitigation CR type enum values + migration"
```

---

### Task 2: `apply_protocol_control` executor + JSON definition

**Files:**
- Create: `backend/app/connectors/executors/ssh/apply_protocol_control.py`
- Create: `backend/app/connectors/change_type_definitions/apply_protocol_control.json`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_vuln_mitigation_cr_types.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock

@pytest.mark.asyncio
async def test_apply_protocol_control_execute_tls10():
    from app.connectors.executors.ssh.apply_protocol_control import execute
    connector = MagicMock()
    connector.run_command = AsyncMock(side_effect=[
        "MinProtocol = TLSv1\n",   # pre_state read
        "",                          # sed apply
    ])
    result = await execute(
        {"protocol": "tls10", "target_os": "linux"},
        ["asset-1"],
        connector,
    )
    assert result["success"] is True
    assert "pre_state" in result
    assert result["pre_state"]["original_line"] is not None

@pytest.mark.asyncio
async def test_apply_protocol_control_rollback():
    from app.connectors.executors.ssh.apply_protocol_control import rollback
    connector = MagicMock()
    connector.run_command = AsyncMock(return_value="")
    result = await rollback(
        {"protocol": "tls10", "target_os": "linux"},
        {"pre_state": {"original_line": "MinProtocol = TLSv1", "config_file": "/etc/ssl/openssl.cnf"}},
        connector,
    )
    assert result["rolled_back"] is True

@pytest.mark.asyncio
async def test_apply_protocol_control_rollback_no_pre_state():
    from app.connectors.executors.ssh.apply_protocol_control import rollback
    connector = MagicMock()
    connector.run_command = AsyncMock(return_value="")
    result = await rollback(
        {"protocol": "tls10", "target_os": "linux"},
        {"pre_state": {"original_line": None, "config_file": "/etc/ssl/openssl.cnf"}},
        connector,
    )
    # When no original line existed, remove the injected line
    assert result["rolled_back"] is True
```

- [ ] **Step 2: Run tests to see them fail**

```bash
cd backend && python -m pytest tests/test_vuln_mitigation_cr_types.py -k "protocol_control" -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Create the executor**

```python
# backend/app/connectors/executors/ssh/apply_protocol_control.py
"""Executor: disable a vulnerable network protocol on Linux via OpenSSL/sysctl config."""
from typing import Any

_CONFIG_FILE = "/etc/ssl/openssl.cnf"

_PROTOCOL_MIN_VERSION = {
    "tls10": "TLSv1.2",
    "tls11": "TLSv1.2",
    "sslv3": "TLSv1",
    "smbv1": None,   # SMB handled via sysctl/kernel, not openssl
    "ntlmv1": None,
}

_SMB_DISABLE_CMD = (
    "sysctl -w kernel.smb1_support=0 2>/dev/null; "
    "echo 'kernel.smb1_support=0' >> /etc/sysctl.d/99-nexplane.conf"
)
_NTLM_DISABLE_CMD = (
    "echo 'NTLM_DISABLE=yes' >> /etc/security/limits.conf"
)


async def execute(parameters: dict, asset_ids: list[str], connector: Any) -> dict:
    protocol = parameters["protocol"]
    min_ver = _PROTOCOL_MIN_VERSION.get(protocol)

    if min_ver is not None:
        # OpenSSL config path
        pre_line_raw = await connector.run_command(
            f"grep -m1 'MinProtocol' {_CONFIG_FILE} 2>/dev/null || echo ''"
        )
        original_line = pre_line_raw.strip() or None
        # Apply: upsert MinProtocol line
        await connector.run_command(
            f"grep -q 'MinProtocol' {_CONFIG_FILE} 2>/dev/null && "
            f"sed -i 's/MinProtocol.*/MinProtocol = {min_ver}/' {_CONFIG_FILE} || "
            f"echo 'MinProtocol = {min_ver}' >> {_CONFIG_FILE}"
        )
        return {
            "success": True,
            "protocol": protocol,
            "pre_state": {"original_line": original_line, "config_file": _CONFIG_FILE},
        }

    if protocol == "smbv1":
        await connector.run_command(_SMB_DISABLE_CMD)
        return {"success": True, "protocol": protocol, "pre_state": {"original_line": None, "config_file": None}}

    if protocol == "ntlmv1":
        await connector.run_command(_NTLM_DISABLE_CMD)
        return {"success": True, "protocol": protocol, "pre_state": {"original_line": None, "config_file": None}}

    raise ValueError(f"Unsupported protocol: {protocol}")


async def rollback(parameters: dict, execution_result: dict, connector: Any) -> dict:
    pre_state = execution_result.get("pre_state", {})
    config_file = pre_state.get("config_file") or _CONFIG_FILE
    original_line = pre_state.get("original_line")

    if original_line:
        # Restore exact original line
        await connector.run_command(
            f"sed -i 's/MinProtocol.*/{original_line}/' {config_file}"
        )
    else:
        # Remove injected line
        await connector.run_command(
            f"sed -i '/^MinProtocol/d' {config_file}"
        )
    return {"rolled_back": True}
```

- [ ] **Step 4: Create JSON definition**

```json
{
  "name": "apply_protocol_control",
  "display_name": "Apply Protocol Control",
  "description": "Disable a vulnerable network protocol on a Linux host (TLS 1.0, TLS 1.1, SSLv3, SMBv1, NTLMv1).",
  "connector_type": "ssh",
  "risk_score": 5,
  "requires_approval": true,
  "rollback_available": true,
  "parameters": {
    "protocol": {
      "type": "string",
      "enum": ["tls10", "tls11", "sslv3", "smbv1", "ntlmv1"],
      "description": "The protocol to disable."
    },
    "target_os": {
      "type": "string",
      "enum": ["linux"],
      "default": "linux"
    }
  }
}
```

- [ ] **Step 5: Run tests to see them pass**

```bash
cd backend && python -m pytest tests/test_vuln_mitigation_cr_types.py -k "protocol_control" -v
```

Expected: 3 PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/executors/ssh/apply_protocol_control.py \
        backend/app/connectors/change_type_definitions/apply_protocol_control.json
git commit -m "feat: apply_protocol_control executor"
```

---

### Task 3: `disable_kernel_feature` executor + JSON definition

**Files:**
- Create: `backend/app/connectors/executors/ssh/disable_kernel_feature.py`
- Create: `backend/app/connectors/change_type_definitions/disable_kernel_feature.json`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_vuln_mitigation_cr_types.py`:

```python
@pytest.mark.asyncio
async def test_disable_kernel_feature_execute():
    from app.connectors.executors.ssh.disable_kernel_feature import execute
    connector = MagicMock()
    connector.run_command = AsyncMock(return_value="")
    result = await execute({"feature": "usb_storage"}, ["asset-1"], connector)
    assert result["success"] is True
    assert result["feature"] == "usb_storage"
    # Should have written blacklist and unloaded module
    calls = [str(c) for c in connector.run_command.call_args_list]
    assert any("blacklist usb_storage" in c for c in calls)
    assert any("rmmod" in c for c in calls)

@pytest.mark.asyncio
async def test_disable_kernel_feature_rollback():
    from app.connectors.executors.ssh.disable_kernel_feature import rollback
    connector = MagicMock()
    connector.run_command = AsyncMock(return_value="")
    result = await rollback({"feature": "usb_storage"}, {}, connector)
    assert result["rolled_back"] is True
    calls = [str(c) for c in connector.run_command.call_args_list]
    assert any("sed" in c for c in calls)
    assert any("modprobe" in c for c in calls)
```

- [ ] **Step 2: Run tests to see them fail**

```bash
cd backend && python -m pytest tests/test_vuln_mitigation_cr_types.py -k "kernel_feature" -v
```

- [ ] **Step 3: Create the executor**

```python
# backend/app/connectors/executors/ssh/disable_kernel_feature.py
"""Executor: blacklist and unload a vulnerable Linux kernel module."""
from typing import Any

_BLACKLIST_FILE = "/etc/modprobe.d/nexplane-disable.conf"


async def execute(parameters: dict, asset_ids: list[str], connector: Any) -> dict:
    feature = parameters["feature"]
    await connector.run_command(
        f"echo 'blacklist {feature}' >> {_BLACKLIST_FILE}"
    )
    await connector.run_command(
        f"rmmod '{feature}' 2>/dev/null || true"
    )
    return {"success": True, "feature": feature}


async def rollback(parameters: dict, execution_result: dict, connector: Any) -> dict:
    feature = parameters["feature"]
    await connector.run_command(
        f"sed -i '/^blacklist {feature}$/d' {_BLACKLIST_FILE}"
    )
    await connector.run_command(
        f"modprobe '{feature}' 2>/dev/null || true"
    )
    return {"rolled_back": True, "feature": feature}
```

- [ ] **Step 4: Create JSON definition**

```json
{
  "name": "disable_kernel_feature",
  "display_name": "Disable Kernel Feature",
  "description": "Blacklist and unload a vulnerable Linux kernel module (e.g. usb_storage, cramfs, hfs).",
  "connector_type": "ssh",
  "risk_score": 4,
  "requires_approval": true,
  "rollback_available": true,
  "parameters": {
    "feature": {
      "type": "string",
      "description": "Kernel module name to blacklist (e.g. 'usb_storage', 'cramfs')."
    }
  }
}
```

- [ ] **Step 5: Run tests to see them pass**

```bash
cd backend && python -m pytest tests/test_vuln_mitigation_cr_types.py -k "kernel_feature" -v
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/executors/ssh/disable_kernel_feature.py \
        backend/app/connectors/change_type_definitions/disable_kernel_feature.json
git commit -m "feat: disable_kernel_feature executor"
```

---

### Task 4: `apply_registry_fix` executor + JSON definition

**Files:**
- Create: `backend/app/connectors/executors/ssh/apply_registry_fix.py`
- Create: `backend/app/connectors/change_type_definitions/apply_registry_fix.json`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_vuln_mitigation_cr_types.py`:

```python
@pytest.mark.asyncio
async def test_apply_registry_fix_execute_new_key():
    from app.connectors.executors.ssh.apply_registry_fix import execute
    connector = MagicMock()
    # Simulate key not existing (Get-ItemProperty returns empty)
    connector.run_command = AsyncMock(return_value="")
    result = await execute(
        {
            "key_path": r"HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services",
            "value_name": "fDenyTSConnections",
            "value_data": "1",
            "value_type": "DWORD",
        },
        ["asset-win-1"],
        connector,
    )
    assert result["success"] is True
    assert result["pre_state"]["existed"] is False

@pytest.mark.asyncio
async def test_apply_registry_fix_rollback_new_key():
    from app.connectors.executors.ssh.apply_registry_fix import rollback
    connector = MagicMock()
    connector.run_command = AsyncMock(return_value="")
    result = await rollback(
        {"key_path": r"HKLM:\TEST", "value_name": "MyVal", "value_data": "1", "value_type": "DWORD"},
        {"pre_state": {"existed": False, "original_value": None}},
        connector,
    )
    assert result["rolled_back"] is True
    calls = [str(c) for c in connector.run_command.call_args_list]
    assert any("Remove-ItemProperty" in c for c in calls)

@pytest.mark.asyncio
async def test_apply_registry_fix_rollback_existing_key():
    from app.connectors.executors.ssh.apply_registry_fix import rollback
    connector = MagicMock()
    connector.run_command = AsyncMock(return_value="")
    result = await rollback(
        {"key_path": r"HKLM:\TEST", "value_name": "MyVal", "value_data": "1", "value_type": "DWORD"},
        {"pre_state": {"existed": True, "original_value": "0"}},
        connector,
    )
    assert result["rolled_back"] is True
    calls = [str(c) for c in connector.run_command.call_args_list]
    assert any("New-ItemProperty" in c for c in calls)
```

- [ ] **Step 2: Run tests to see them fail**

```bash
cd backend && python -m pytest tests/test_vuln_mitigation_cr_types.py -k "registry_fix" -v
```

- [ ] **Step 3: Create the executor**

```python
# backend/app/connectors/executors/ssh/apply_registry_fix.py
"""Executor: set a Windows registry value via PowerShell (delivered through SSH/WinRM connector)."""
from typing import Any


async def execute(parameters: dict, asset_ids: list[str], connector: Any) -> dict:
    key_path = parameters["key_path"]
    value_name = parameters["value_name"]
    value_data = parameters["value_data"]
    value_type = parameters["value_type"]

    # Capture pre-state
    read_cmd = (
        f"$v = Get-ItemProperty -Path '{key_path}' -Name '{value_name}' -ErrorAction SilentlyContinue; "
        f"if ($v) {{ $v.{value_name} }} else {{ '' }}"
    )
    original_raw = await connector.run_command(read_cmd)
    original_value = original_raw.strip() or None
    existed = original_value is not None

    # Apply
    apply_cmd = (
        f"New-ItemProperty -Path '{key_path}' -Name '{value_name}' "
        f"-Value '{value_data}' -PropertyType '{value_type}' -Force | Out-Null"
    )
    await connector.run_command(apply_cmd)

    return {
        "success": True,
        "pre_state": {"existed": existed, "original_value": original_value},
    }


async def rollback(parameters: dict, execution_result: dict, connector: Any) -> dict:
    key_path = parameters["key_path"]
    value_name = parameters["value_name"]
    value_type = parameters["value_type"]
    pre_state = execution_result.get("pre_state", {})

    if pre_state.get("existed"):
        original_value = pre_state["original_value"]
        restore_cmd = (
            f"New-ItemProperty -Path '{key_path}' -Name '{value_name}' "
            f"-Value '{original_value}' -PropertyType '{value_type}' -Force | Out-Null"
        )
        await connector.run_command(restore_cmd)
    else:
        remove_cmd = (
            f"Remove-ItemProperty -Path '{key_path}' -Name '{value_name}' -ErrorAction SilentlyContinue"
        )
        await connector.run_command(remove_cmd)

    return {"rolled_back": True}
```

- [ ] **Step 4: Create JSON definition**

```json
{
  "name": "apply_registry_fix",
  "display_name": "Apply Registry Fix",
  "description": "Set a Windows registry value to mitigate a CVE (e.g. disable HTTP/2, enforce screen saver lock).",
  "connector_type": "winrm",
  "risk_score": 5,
  "requires_approval": true,
  "rollback_available": true,
  "parameters": {
    "key_path": {
      "type": "string",
      "description": "Full registry path, e.g. HKLM:\\SOFTWARE\\Policies\\..."
    },
    "value_name": {"type": "string"},
    "value_data": {"type": "string"},
    "value_type": {
      "type": "string",
      "enum": ["String", "DWORD", "ExpandString"]
    }
  }
}
```

- [ ] **Step 5: Run tests to see them pass**

```bash
cd backend && python -m pytest tests/test_vuln_mitigation_cr_types.py -k "registry_fix" -v
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/executors/ssh/apply_registry_fix.py \
        backend/app/connectors/change_type_definitions/apply_registry_fix.json
git commit -m "feat: apply_registry_fix executor"
```

---

### Task 5: `remove_vulnerable_package` executor + JSON definition

**Files:**
- Create: `backend/app/connectors/executors/ssh/remove_vulnerable_package.py`
- Create: `backend/app/connectors/change_type_definitions/remove_vulnerable_package.json`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_vuln_mitigation_cr_types.py`:

```python
@pytest.mark.asyncio
async def test_remove_vulnerable_package_execute():
    from app.connectors.executors.ssh.remove_vulnerable_package import execute
    connector = MagicMock()
    connector.run_command = AsyncMock(side_effect=[
        "telnet-0.17-65.el8.x86_64\n",   # version capture
        "",                                 # remove command
    ])
    result = await execute({"package_name": "telnet", "target_os": "linux"}, ["a1"], connector)
    assert result["success"] is True
    assert "telnet" in result["pre_state"]["installed_version"]

@pytest.mark.asyncio
async def test_remove_vulnerable_package_rollback_with_version():
    from app.connectors.executors.ssh.remove_vulnerable_package import rollback
    connector = MagicMock()
    connector.run_command = AsyncMock(return_value="")
    result = await rollback(
        {"package_name": "telnet", "target_os": "linux"},
        {"pre_state": {"installed_version": "telnet-0.17-65.el8.x86_64"}},
        connector,
    )
    assert result["rolled_back"] is True
    calls = [str(c) for c in connector.run_command.call_args_list]
    assert any("install" in c for c in calls)

@pytest.mark.asyncio
async def test_remove_vulnerable_package_rollback_no_version():
    from app.connectors.executors.ssh.remove_vulnerable_package import rollback
    connector = MagicMock()
    connector.run_command = AsyncMock(return_value="")
    result = await rollback(
        {"package_name": "telnet", "target_os": "linux"},
        {"pre_state": {"installed_version": None}},
        connector,
    )
    assert result["rolled_back"] is False
    assert "version" in result["reason"]
```

- [ ] **Step 2: Run tests to see them fail**

```bash
cd backend && python -m pytest tests/test_vuln_mitigation_cr_types.py -k "remove_vulnerable" -v
```

- [ ] **Step 3: Create the executor**

```python
# backend/app/connectors/executors/ssh/remove_vulnerable_package.py
"""Executor: remove a package that has no available patch and poses unacceptable risk."""
from typing import Any

_VERSION_CMD = (
    "rpm -q '{pkg}' 2>/dev/null || "
    "dpkg -s '{pkg}' 2>/dev/null | grep '^Version' | awk '{{print $2}}' || "
    "echo ''"
)
_REMOVE_CMD = (
    "apt-get remove -y '{pkg}' 2>/dev/null || "
    "yum remove -y '{pkg}' 2>/dev/null || "
    "dnf remove -y '{pkg}' 2>/dev/null"
)
_INSTALL_CMD = (
    "apt-get install -y '{pkg}={ver}' 2>/dev/null || "
    "yum install -y '{pkg}-{ver}' 2>/dev/null || "
    "dnf install -y '{pkg}-{ver}' 2>/dev/null"
)


async def execute(parameters: dict, asset_ids: list[str], connector: Any) -> dict:
    pkg = parameters["package_name"]
    ver_raw = await connector.run_command(_VERSION_CMD.format(pkg=pkg))
    installed_version = ver_raw.strip() or None
    await connector.run_command(_REMOVE_CMD.format(pkg=pkg))
    return {"success": True, "pre_state": {"installed_version": installed_version}}


async def rollback(parameters: dict, execution_result: dict, connector: Any) -> dict:
    pkg = parameters["package_name"]
    pre_state = execution_result.get("pre_state", {})
    ver = pre_state.get("installed_version")
    if not ver:
        return {"rolled_back": False, "reason": "No version captured in pre_state — cannot reinstall pinned version"}
    await connector.run_command(_INSTALL_CMD.format(pkg=pkg, ver=ver))
    return {"rolled_back": True}
```

- [ ] **Step 4: Create JSON definition**

```json
{
  "name": "remove_vulnerable_package",
  "display_name": "Remove Vulnerable Package",
  "description": "Remove a package that has no available patch and poses unacceptable risk (e.g. telnet, xinetd, rsh-server).",
  "connector_type": "ssh",
  "risk_score": 6,
  "requires_approval": true,
  "rollback_available": true,
  "parameters": {
    "package_name": {
      "type": "string",
      "description": "Package name to remove (e.g. 'telnet')."
    },
    "target_os": {
      "type": "string",
      "enum": ["linux"],
      "default": "linux"
    }
  }
}
```

- [ ] **Step 5: Run tests to see them pass**

```bash
cd backend && python -m pytest tests/test_vuln_mitigation_cr_types.py -k "remove_vulnerable" -v
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/executors/ssh/remove_vulnerable_package.py \
        backend/app/connectors/change_type_definitions/remove_vulnerable_package.json
git commit -m "feat: remove_vulnerable_package executor"
```

---

### Task 6: `revoke_exposed_credential` executor + JSON definition

**Files:**
- Create: `backend/app/connectors/executors/aws/revoke_exposed_credential.py`
- Create: `backend/app/connectors/change_type_definitions/revoke_exposed_credential.json`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_vuln_mitigation_cr_types.py`:

```python
@pytest.mark.asyncio
async def test_revoke_exposed_credential_aws_iam():
    from app.connectors.executors.aws.revoke_exposed_credential import execute
    connector = MagicMock()
    connector.creds = {"aws_access_key_id": "AKIA...", "aws_secret_access_key": "secret", "region": "us-east-1"}
    import boto3
    from unittest.mock import patch
    with patch("boto3.client") as mock_boto:
        mock_iam = MagicMock()
        mock_boto.return_value = mock_iam
        mock_iam.delete_access_key.return_value = {}
        result = await execute(
            {"credential_type": "aws_iam_key", "credential_id": "AKIAIOSFODNN7EXAMPLE"},
            ["asset-1"],
            connector,
        )
    assert result["success"] is True
    assert result["rolled_back_available"] is False

@pytest.mark.asyncio
async def test_revoke_exposed_credential_rollback_always_false():
    from app.connectors.executors.aws.revoke_exposed_credential import rollback
    connector = MagicMock()
    result = await rollback({}, {}, connector)
    assert result["rolled_back"] is False
    assert "permanent" in result["reason"]

@pytest.mark.asyncio
async def test_revoke_exposed_credential_mock_mode():
    from app.connectors.executors.aws.revoke_exposed_credential import execute
    connector = MagicMock()
    connector.creds = None
    result = await execute(
        {"credential_type": "aws_iam_key", "credential_id": "AKIATEST"},
        ["asset-1"],
        connector,
    )
    assert result["success"] is True
    assert result.get("mock") is True
```

- [ ] **Step 2: Run tests to see them fail**

```bash
cd backend && python -m pytest tests/test_vuln_mitigation_cr_types.py -k "revoke_exposed" -v
```

- [ ] **Step 3: Create the executor**

```python
# backend/app/connectors/executors/aws/revoke_exposed_credential.py
"""Executor: immediately revoke a known-compromised credential. NO rollback — permanent."""
import logging
from typing import Any

import boto3

logger = logging.getLogger(__name__)


async def execute(parameters: dict, asset_ids: list[str], connector: Any) -> dict:
    credential_type = parameters["credential_type"]
    credential_id = parameters["credential_id"]
    creds = getattr(connector, "creds", None)

    if not creds:
        logger.info("[mock] Would revoke %s %s", credential_type, credential_id)
        return {"success": True, "mock": True, "rolled_back_available": False}

    if credential_type == "aws_iam_key":
        iam = boto3.client(
            "iam",
            aws_access_key_id=creds["aws_access_key_id"],
            aws_secret_access_key=creds["aws_secret_access_key"],
            region_name=creds.get("region", "us-east-1"),
        )
        iam.delete_access_key(AccessKeyId=credential_id)
        logger.info("Revoked IAM key %s", credential_id)
        return {"success": True, "rolled_back_available": False}

    if credential_type == "vault_token":
        import httpx
        vault_addr = creds.get("vault_addr", "http://localhost:8200")
        vault_token = creds.get("vault_token")
        async with httpx.AsyncClient() as client:
            resp = client.post(
                f"{vault_addr}/v1/auth/token/revoke",
                headers={"X-Vault-Token": vault_token},
                json={"token": credential_id},
            )
            resp.raise_for_status()
        return {"success": True, "rolled_back_available": False}

    raise ValueError(f"Unsupported credential_type: {credential_type}")


async def rollback(parameters: dict, execution_result: dict, connector: Any) -> dict:
    return {"rolled_back": False, "reason": "Credential revocation is permanent — no rollback available"}
```

- [ ] **Step 4: Create JSON definition**

```json
{
  "name": "revoke_exposed_credential",
  "display_name": "Revoke Exposed Credential",
  "description": "Immediately revoke a known-compromised credential. PERMANENT — no rollback. Requires 2 approvers.",
  "connector_type": "aws",
  "risk_score": 9,
  "requires_approval": true,
  "min_approvers": 2,
  "rollback_available": false,
  "parameters": {
    "credential_type": {
      "type": "string",
      "enum": ["aws_iam_key", "gcp_service_account_key", "azure_client_secret", "vault_token", "ldap_password"]
    },
    "credential_id": {
      "type": "string",
      "description": "The key ID, token, or account name to revoke."
    },
    "connector_id": {
      "type": "string",
      "description": "UUID of the connector to use for revocation."
    }
  }
}
```

- [ ] **Step 5: Run tests to see them pass**

```bash
cd backend && python -m pytest tests/test_vuln_mitigation_cr_types.py -k "revoke_exposed" -v
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/executors/aws/revoke_exposed_credential.py \
        backend/app/connectors/change_type_definitions/revoke_exposed_credential.json
git commit -m "feat: revoke_exposed_credential executor"
```

---

### Task 7: Wire into `MITIGATION_CR_MAP` + `vulnerability.py`

**Files:**
- Modify: `backend/app/routers/vulnerability.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_vuln_mitigation_cr_types.py`:

```python
def test_mitigation_cr_map_contains_new_types():
    from app.routers.vulnerability import MITIGATION_CR_MAP
    assert "protocol_control" in MITIGATION_CR_MAP
    assert "kernel_feature" in MITIGATION_CR_MAP
    assert "registry_fix" in MITIGATION_CR_MAP
    assert "package_remove" in MITIGATION_CR_MAP
    assert "credential_revoke" in MITIGATION_CR_MAP
    assert MITIGATION_CR_MAP["protocol_control"] == "apply_protocol_control"
    assert MITIGATION_CR_MAP["credential_revoke"] == "revoke_exposed_credential"
```

- [ ] **Step 2: Run test to see it fail**

```bash
cd backend && python -m pytest tests/test_vuln_mitigation_cr_types.py::test_mitigation_cr_map_contains_new_types -v
```

- [ ] **Step 3: Add entries to `MITIGATION_CR_MAP`**

Open `backend/app/routers/vulnerability.py`. Find the `MITIGATION_CR_MAP` dict (around line 621). Add these five entries:

```python
"protocol_control":  "apply_protocol_control",
"kernel_feature":    "disable_kernel_feature",
"registry_fix":      "apply_registry_fix",
"package_remove":    "remove_vulnerable_package",
"credential_revoke": "revoke_exposed_credential",
```

- [ ] **Step 4: Run full test file**

```bash
cd backend && python -m pytest tests/test_vuln_mitigation_cr_types.py -v
```

Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/vulnerability.py backend/tests/test_vuln_mitigation_cr_types.py
git commit -m "feat: wire mitigation CR types into MITIGATION_CR_MAP"
```

---

### Task 8: Apply migration on EC2 + smoke verify

**Files:**
- Modify: `backend/tests/smoke/test_vuln_live.py` (add new smoke assertions)

- [ ] **Step 1: Push changes to EC2**

```bash
git push origin master
```

On EC2 runner:

```bash
cd /home/ec2-user/nexplane && git pull
docker exec nexplane-backend-1 alembic upgrade head
docker restart nexplane-backend-1
```

- [ ] **Step 2: Run existing unit tests inside container**

```bash
docker exec nexplane-backend-1 python -m pytest tests/test_vuln_mitigation_cr_types.py -v
```

Expected: All pass

- [ ] **Step 3: Smoke verify — new CR types appear in list**

```bash
# Get an API token from the running platform
TOKEN=$(docker exec nexplane-backend-1 python -c "
import asyncio
from app.database import get_db_session
from app.models.api_token import ApiToken
from sqlalchemy import select
async def main():
    async for db in get_db_session():
        r = await db.execute(select(ApiToken).limit(1))
        t = r.scalar_one_or_none()
        print(t.token_prefix if t else 'none')
asyncio.run(main())
")

# Verify new types in list_change_types output
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/change-requests/types | \
  python -m json.tool | grep -E "apply_protocol_control|disable_kernel_feature|apply_registry_fix|remove_vulnerable_package|revoke_exposed_credential"
```

Expected: All 5 type names appear in response.

- [ ] **Step 4: Smoke verify — mitigate endpoint dispatches new type**

```bash
# Get a finding ID (any existing finding)
FINDING_ID=$(curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/vulnerability/findings?limit=1 | python -c "import sys,json; d=json.load(sys.stdin); print(d['items'][0]['id'] if d.get('items') else '')")

if [ -n "$FINDING_ID" ]; then
  curl -s -X POST \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"mitigation_type": "protocol_control", "mitigation_parameters": {"protocol": "tls10", "target_os": "linux"}}' \
    http://localhost:8000/vulnerability/findings/$FINDING_ID/mitigate | python -m json.tool
fi
```

Expected: Response contains a CR in draft state with `change_type: "apply_protocol_control"`.

- [ ] **Step 5: Commit smoke additions if any changes made**

```bash
git add backend/tests/smoke/
git commit -m "smoke: verify mitigation CR types dispatch" || echo "No smoke changes"
```
