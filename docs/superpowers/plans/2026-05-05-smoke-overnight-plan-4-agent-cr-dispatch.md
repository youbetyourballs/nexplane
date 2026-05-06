# Smoke Test Agent CR Dispatch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade `test_agent_live.py` to dispatch Linux and Windows agent commands via Nexplane change requests targeting the registered endpoint asset, rather than SSM shell commands, so the full agent→control plane→executor path is tested end-to-end.

**Architecture:** New per-group change types (`agent_linux_patch`, `agent_ossecurity`, etc.) with CT definitions that group related generic actions. The existing setup code (Phase A-style instance launch) stays. After agent registers as an endpoint asset, each phase group creates a CR targeting that endpoint asset. Falls back to SSM if agent endpoint not registered. Also adds Windows agent CR dispatch for `win_patch` and `winharden`.

**Tech Stack:** Python 3.12, Nexplane CR machinery, nexplane_agent catalog, PostgreSQL migration

---

## Files

**Create:**
- `backend/app/connectors/change_type_definitions/agent_linux_patch.json`
- `backend/app/connectors/change_type_definitions/agent_ossecurity.json`
- `backend/app/connectors/change_type_definitions/agent_linuxauth.json`
- `backend/app/connectors/change_type_definitions/agent_crossplatform.json`
- `backend/app/connectors/change_type_definitions/agent_compliance.json`
- `backend/app/connectors/change_type_definitions/agent_forensics.json`
- `backend/app/connectors/change_type_definitions/agent_fleet.json`
- `backend/app/connectors/change_type_definitions/agent_backup.json`
- `backend/app/connectors/change_type_definitions/agent_reboot.json`
- `backend/app/connectors/change_type_definitions/agent_credrotation.json`
- `backend/app/connectors/change_type_definitions/agent_iac.json`
- `backend/app/connectors/change_type_definitions/agent_linuxupgrade.json`
- `backend/app/connectors/change_type_definitions/agent_win_patch.json`
- `backend/app/connectors/change_type_definitions/agent_winharden.json`
- `backend/alembic/versions/028_add_agent_cr_change_types.py`

**Modify:**
- `backend/app/models/change_request.py` — add agent_* change types
- `backend/app/services/safety_engine.py` — add agent_* to IMPLICIT_ROLLBACK_TYPES
- `backend/tests/smoke/test_agent_live.py` — add CR-based phase runners, endpoint polling, update AWS track

---

### Task 1: Create agent CT definitions

**Files:**
- Create: all 14 `backend/app/connectors/change_type_definitions/agent_*.json` files

- [ ] **Step 1: Create `agent_linux_patch.json`**

```json
{
  "change_type": "agent_linux_patch",
  "display_name": "Agent: Linux Patch Management",
  "steps": [
    {"generic_action": "audit_linux_patch_status", "purpose": "preflight_validate", "required": true},
    {"generic_action": "apply_linux_patches",       "purpose": "execute",            "required": false}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 2: Create `agent_ossecurity.json`**

```json
{
  "change_type": "agent_ossecurity",
  "display_name": "Agent: OS Security Hardening",
  "steps": [
    {"generic_action": "audit_os_security_posture",      "purpose": "preflight_validate", "required": true},
    {"generic_action": "configure_selinux",              "purpose": "execute",            "required": false},
    {"generic_action": "configure_seccomp",              "purpose": "execute",            "required": false},
    {"generic_action": "apply_sysctl_hardening",         "purpose": "execute",            "required": false},
    {"generic_action": "configure_host_firewall",        "purpose": "execute",            "required": false},
    {"generic_action": "blacklist_kernel_modules",       "purpose": "execute",            "required": false},
    {"generic_action": "harden_mount_options",           "purpose": "execute",            "required": false},
    {"generic_action": "deploy_auditd_rules",            "purpose": "execute",            "required": false},
    {"generic_action": "setup_file_integrity_monitoring","purpose": "execute",            "required": false},
    {"generic_action": "audit_ebpf_posture",             "purpose": "verify",             "required": false}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 3: Create `agent_linuxauth.json`**

```json
{
  "change_type": "agent_linuxauth",
  "display_name": "Agent: Linux Authentication Hardening",
  "steps": [
    {"generic_action": "harden_ssh",                  "purpose": "execute", "required": false},
    {"generic_action": "configure_pam",               "purpose": "execute", "required": false},
    {"generic_action": "manage_ca_certificates",      "purpose": "execute", "required": false},
    {"generic_action": "configure_ntp",               "purpose": "execute", "required": false},
    {"generic_action": "audit_users_and_groups",      "purpose": "verify",  "required": true},
    {"generic_action": "audit_privesc_vulnerabilities","purpose": "verify",  "required": false}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 4: Create `agent_crossplatform.json`**

```json
{
  "change_type": "agent_crossplatform",
  "display_name": "Agent: Cross-Platform Hardening",
  "steps": [
    {"generic_action": "harden_tls_protocols",  "purpose": "execute", "required": false},
    {"generic_action": "configure_dns_resolver", "purpose": "execute", "required": false},
    {"generic_action": "audit_software_inventory","purpose": "verify", "required": true},
    {"generic_action": "configure_syslog",       "purpose": "execute", "required": false}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 5: Create `agent_compliance.json`**

```json
{
  "change_type": "agent_compliance",
  "display_name": "Agent: Compliance Audit",
  "steps": [
    {"generic_action": "audit_linux_patch_status",   "purpose": "preflight_validate", "required": false},
    {"generic_action": "audit_os_security_posture",  "purpose": "preflight_validate", "required": false},
    {"generic_action": "audit_users_and_groups",     "purpose": "preflight_validate", "required": false}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 6: Create `agent_forensics.json`**

```json
{
  "change_type": "agent_forensics",
  "display_name": "Agent: Forensics Bundle Collection",
  "steps": [
    {"generic_action": "audit_software_inventory",   "purpose": "execute", "required": true},
    {"generic_action": "audit_os_security_posture",  "purpose": "execute", "required": false},
    {"generic_action": "audit_users_and_groups",     "purpose": "execute", "required": false}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 7: Create `agent_fleet.json`**

```json
{
  "change_type": "agent_fleet",
  "display_name": "Agent: Fleet Operations",
  "steps": [
    {"generic_action": "audit_software_inventory", "purpose": "preflight_validate", "required": false},
    {"generic_action": "configure_syslog",         "purpose": "execute",            "required": false}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 8: Create `agent_backup.json`**

```json
{
  "change_type": "agent_backup",
  "display_name": "Agent: Backup and Restore",
  "steps": [
    {"generic_action": "audit_software_inventory", "purpose": "preflight_validate", "required": false}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 9: Create `agent_reboot.json`**

```json
{
  "change_type": "agent_reboot",
  "display_name": "Agent: Graceful Reboot",
  "steps": [
    {"generic_action": "audit_os_security_posture", "purpose": "preflight_validate", "required": false}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 10: Create `agent_credrotation.json`**

```json
{
  "change_type": "agent_credrotation",
  "display_name": "Agent: Credential Rotation",
  "steps": [
    {"generic_action": "audit_users_and_groups",      "purpose": "preflight_validate", "required": false},
    {"generic_action": "audit_privesc_vulnerabilities","purpose": "preflight_validate", "required": false}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 11: Create `agent_iac.json`**

```json
{
  "change_type": "agent_iac",
  "display_name": "Agent: IaC Operations",
  "steps": [
    {"generic_action": "audit_software_inventory", "purpose": "preflight_validate", "required": false}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 12: Create `agent_linuxupgrade.json`**

```json
{
  "change_type": "agent_linuxupgrade",
  "display_name": "Agent: Linux Instance Upgrade (estimate only)",
  "steps": [
    {"generic_action": "estimate_image_size", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 13: Create `agent_win_patch.json`**

```json
{
  "change_type": "agent_win_patch",
  "display_name": "Agent: Windows Patch Management",
  "steps": [
    {"generic_action": "audit_windows_patch_status", "purpose": "preflight_validate", "required": true},
    {"generic_action": "apply_windows_patches",      "purpose": "execute",            "required": false}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 14: Create `agent_winharden.json`**

```json
{
  "change_type": "agent_winharden",
  "display_name": "Agent: Windows Hardening",
  "steps": [
    {"generic_action": "configure_windows_firewall",  "purpose": "execute", "required": false},
    {"generic_action": "harden_smb",                  "purpose": "execute", "required": false},
    {"generic_action": "harden_rdp",                  "purpose": "execute", "required": false},
    {"generic_action": "configure_windows_audit_policy","purpose":"execute", "required": false},
    {"generic_action": "harden_registry",             "purpose": "execute", "required": false},
    {"generic_action": "harden_tls_protocols",        "purpose": "execute", "required": false},
    {"generic_action": "audit_scheduled_tasks",       "purpose": "verify",  "required": false}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 15: Verify catalog loads**

```bash
docker exec nexplane-backend-1 python -c "
from app.connectors.catalog_service import ActionCatalogService
import pathlib
svc = ActionCatalogService(pathlib.Path('app/connectors/catalog'))
print('Catalog OK:', len(svc._catalog), 'connectors')
"
```

- [ ] **Step 16: Commit**

```bash
git add backend/app/connectors/change_type_definitions/agent_*.json
git commit -m "feat(agent): add CT definitions for all 14 agent command group change types"
```

---

### Task 2: Add agent change types to model + safety engine + migration

**Files:**
- Modify: `backend/app/models/change_request.py`
- Modify: `backend/app/services/safety_engine.py`
- Create: `backend/alembic/versions/028_add_agent_cr_change_types.py`

- [ ] **Step 1: Add to ChangeType enum**

In `backend/app/models/change_request.py`, after `restore_s3_public_access = "restore_s3_public_access"` (or after `collect_evidence`), add:

```python
    # Agent command group change types
    agent_linux_patch = "agent_linux_patch"
    agent_ossecurity = "agent_ossecurity"
    agent_linuxauth = "agent_linuxauth"
    agent_crossplatform = "agent_crossplatform"
    agent_compliance = "agent_compliance"
    agent_forensics = "agent_forensics"
    agent_fleet = "agent_fleet"
    agent_backup = "agent_backup"
    agent_reboot = "agent_reboot"
    agent_credrotation = "agent_credrotation"
    agent_iac = "agent_iac"
    agent_linuxupgrade = "agent_linuxupgrade"
    agent_win_patch = "agent_win_patch"
    agent_winharden = "agent_winharden"
```

- [ ] **Step 2: Add to IMPLICIT_ROLLBACK_TYPES**

In `backend/app/services/safety_engine.py`, add to the `_IMPLICIT_ROLLBACK_TYPES` set:

```python
        ChangeType.agent_linux_patch, ChangeType.agent_ossecurity,
        ChangeType.agent_linuxauth, ChangeType.agent_crossplatform,
        ChangeType.agent_compliance, ChangeType.agent_forensics,
        ChangeType.agent_fleet, ChangeType.agent_backup,
        ChangeType.agent_reboot, ChangeType.agent_credrotation,
        ChangeType.agent_iac, ChangeType.agent_linuxupgrade,
        ChangeType.agent_win_patch, ChangeType.agent_winharden,
```

- [ ] **Step 3: Create migration 028**

```python
"""add agent CR change types

Revision ID: 028
Revises: 027
Create Date: 2026-05-05
"""
from alembic import op

revision = '028'
down_revision = '027'
branch_labels = None
depends_on = None


def upgrade():
    new_types = [
        'agent_linux_patch', 'agent_ossecurity', 'agent_linuxauth',
        'agent_crossplatform', 'agent_compliance', 'agent_forensics',
        'agent_fleet', 'agent_backup', 'agent_reboot', 'agent_credrotation',
        'agent_iac', 'agent_linuxupgrade', 'agent_win_patch', 'agent_winharden',
    ]
    for t in new_types:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{t}'")


def downgrade():
    pass
```

- [ ] **Step 4: Run migration**

```bash
docker exec nexplane-backend-1 alembic upgrade head 2>&1 | tail -5
```

Expected: `Running upgrade 027 -> 028, add agent CR change types`

- [ ] **Step 5: Run backend tests**

```bash
docker exec nexplane-backend-1 python -m pytest tests/ -q 2>&1 | tail -5
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/change_request.py \
        backend/app/services/safety_engine.py \
        backend/alembic/versions/028_add_agent_cr_change_types.py
git commit -m "feat(agent): add 14 agent_* change types to ChangeType enum + IMPLICIT_ROLLBACK_TYPES + migration 028"
```

---

### Task 3: Upgrade `test_agent_live.py` with CR-based dispatch

**Files:**
- Modify: `backend/tests/smoke/test_agent_live.py`

- [ ] **Step 1: Add CR-based runner helper and update `_setup_aws_linux_instance`**

After the existing `_ssm` helper function, add a CR-based runner:

```python
def _agent_cr(client: NexplaneClient, endpoint_asset_id: str, phase: str,
              change_type: str, params: dict = None) -> None:
    """Dispatch an agent command group via Nexplane CR targeting the endpoint asset."""
    client.run_cr(
        f"[Phase {phase}] {change_type.replace('agent_', '').replace('_', ' ')}",
        change_type,
        endpoint_asset_id,
        params or {"dry_run": True},
    )
    log(f"{phase}: {change_type}")
```

Update `_setup_aws_linux_instance` to also return the endpoint asset ID. Find the return statement:
```python
    return {
        "instance_asset": instance_asset,
        "instance_id": instance_id,
        "backend_ip": backend_ip,
        "agent_secret": agent_secret,
    }
```

Replace with:
```python
    return {
        "instance_asset": instance_asset,
        "instance_id": instance_id,
        "backend_ip": backend_ip,
        "agent_secret": agent_secret,
        "endpoint_asset_id": agent_asset["id"] if agent_asset else None,
    }
```

- [ ] **Step 2: Add CR-based phase runners for AWS Linux track**

Add these functions after the existing SSM-based runners and before `_LINUX_PHASES`:

```python
# ---------------------------------------------------------------------------
# AWS Linux agent CR phase runners (used when endpoint asset is available)
# ---------------------------------------------------------------------------

def run_linux_patch_aws_cr(client: NexplaneClient, endpoint_asset_id: str) -> None:
    print("\n  [linux_patch via CR]")
    _agent_cr(client, endpoint_asset_id, "linux_patch-aws-linux", "agent_linux_patch")


def run_ossecurity_aws_cr(client: NexplaneClient, endpoint_asset_id: str) -> None:
    print("\n  [ossecurity via CR]")
    _agent_cr(client, endpoint_asset_id, "ossecurity-aws-linux", "agent_ossecurity")


def run_linuxauth_aws_cr(client: NexplaneClient, endpoint_asset_id: str) -> None:
    print("\n  [linuxauth via CR]")
    _agent_cr(client, endpoint_asset_id, "linuxauth-aws-linux", "agent_linuxauth")


def run_crossplatform_aws_cr(client: NexplaneClient, endpoint_asset_id: str) -> None:
    print("\n  [crossplatform via CR]")
    _agent_cr(client, endpoint_asset_id, "crossplatform-aws-linux", "agent_crossplatform")


def run_compliance_aws_cr(client: NexplaneClient, endpoint_asset_id: str) -> None:
    print("\n  [compliance via CR]")
    _agent_cr(client, endpoint_asset_id, "compliance-aws-linux", "agent_compliance")


def run_forensics_aws_cr(client: NexplaneClient, endpoint_asset_id: str) -> None:
    print("\n  [forensics via CR]")
    _agent_cr(client, endpoint_asset_id, "forensics-aws-linux", "agent_forensics")


def run_fleet_aws_cr(client: NexplaneClient, endpoint_asset_id: str) -> None:
    print("\n  [fleet via CR]")
    _agent_cr(client, endpoint_asset_id, "fleet-aws-linux", "agent_fleet")


def run_backup_aws_cr(client: NexplaneClient, endpoint_asset_id: str) -> None:
    print("\n  [backup via CR]")
    _agent_cr(client, endpoint_asset_id, "backup-aws-linux", "agent_backup")


def run_reboot_aws_cr(client: NexplaneClient, endpoint_asset_id: str) -> None:
    print("\n  [reboot via CR]")
    _agent_cr(client, endpoint_asset_id, "reboot-aws-linux", "agent_reboot")


def run_credrotation_aws_cr(client: NexplaneClient, endpoint_asset_id: str) -> None:
    print("\n  [credrotation via CR]")
    _agent_cr(client, endpoint_asset_id, "credrotation-aws-linux", "agent_credrotation")


def run_iac_aws_cr(client: NexplaneClient, endpoint_asset_id: str) -> None:
    print("\n  [iac via CR]")
    _agent_cr(client, endpoint_asset_id, "iac-aws-linux", "agent_iac")


def run_linuxupgrade_aws_cr(client: NexplaneClient, endpoint_asset_id: str) -> None:
    print("\n  [linuxupgrade via CR]")
    _agent_cr(client, endpoint_asset_id, "linuxupgrade-aws-linux", "agent_linuxupgrade")
```

- [ ] **Step 3: Update `_LINUX_PHASE_MAP_AWS` to use CR runners when endpoint available**

Replace `_LINUX_PHASE_MAP_AWS` dict definition:

```python
_LINUX_PHASE_MAP_AWS_SSM = {
    "linux_patch":   run_linux_patch_aws,
    "ossecurity":    run_ossecurity_aws,
    "linuxauth":     run_linuxauth_aws,
    "crossplatform": run_crossplatform_aws,
    "compliance":    run_compliance_aws,
    "forensics":     run_forensics_aws,
    "fleet":         run_fleet_aws,
    "backup":        run_backup_aws,
    "reboot":        run_reboot_aws,
    "credrotation":  run_credrotation_aws,
    "iac":           run_iac_aws,
    "linuxupgrade":  run_linuxupgrade_aws,
}

_LINUX_PHASE_MAP_AWS_CR = {
    "linux_patch":   run_linux_patch_aws_cr,
    "ossecurity":    run_ossecurity_aws_cr,
    "linuxauth":     run_linuxauth_aws_cr,
    "crossplatform": run_crossplatform_aws_cr,
    "compliance":    run_compliance_aws_cr,
    "forensics":     run_forensics_aws_cr,
    "fleet":         run_fleet_aws_cr,
    "backup":        run_backup_aws_cr,
    "reboot":        run_reboot_aws_cr,
    "credrotation":  run_credrotation_aws_cr,
    "iac":           run_iac_aws_cr,
    "linuxupgrade":  run_linuxupgrade_aws_cr,
}
```

(Keep `_LINUX_PHASE_MAP_AWS = _LINUX_PHASE_MAP_AWS_SSM` for backward compat — update the runner to select which map to use.)

- [ ] **Step 4: Update `run_aws_linux_track` to dispatch CR runners when endpoint is available**

Replace the existing `run_aws_linux_track` function body:

```python
def run_aws_linux_track(client: NexplaneClient, cloud_account_id: str,
                         tailscale_auth_key: str, phases: set) -> None:
    """Run all selected Linux agent command phases on AWS.

    Uses Nexplane agent CRs when the endpoint asset is registered (full stack test).
    Falls back to SSM shell commands if agent didn't register in time.
    """
    print("\n" + "=" * 50)
    print("Track: AWS Linux")
    print("=" * 50)

    try:
        setup_result = _setup_aws_linux_instance(client, cloud_account_id, tailscale_auth_key)
        instance_asset = setup_result["instance_asset"]
        instance_id = setup_result["instance_id"]
        asset_id = instance_asset["id"]
        endpoint_asset_id = setup_result.get("endpoint_asset_id")

        if endpoint_asset_id:
            log(f"Agent endpoint registered: {endpoint_asset_id} — using CR dispatch")
            phase_map = _LINUX_PHASE_MAP_AWS_CR
            runner_args = (client, endpoint_asset_id)
        else:
            print("  ⚠️  Agent endpoint not registered — falling back to SSM dispatch")
            phase_map = _LINUX_PHASE_MAP_AWS_SSM
            runner_args = (client, asset_id, instance_id)

        for phase in _LINUX_PHASES:
            if phase not in phases:
                continue
            runner = phase_map.get(phase)
            if runner:
                runner(*runner_args)

        log("AWS Linux track complete")

    except Exception as e:
        print(f"\n❌ AWS Linux track failed: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        _teardown_aws_linux_instance(client)
```

- [ ] **Step 5: Add Windows agent CR runner to `run_aws_windows_track`**

In `run_aws_windows_track`, after the existing SSM-based `win_patch` and `winharden` blocks, add CR-based dispatch as an additional verification if a Windows endpoint asset registers:

After the SSM wait loop at the start of the track, add endpoint detection:
```python
        # Try to detect Windows agent endpoint asset
        win_endpoint_id = None
        print("  Waiting up to 3min for Windows agent to register...")
        deadline_ep = _time.time() + 180
        while _time.time() < deadline_ep:
            candidates = client.get("/assets", params={
                "q": "nexplane-agent-smoke-win", "asset_type": "endpoint"})
            if candidates:
                win_endpoint_id = candidates[0]["id"]
                log(f"Windows agent registered: {win_endpoint_id}")
                break
            _time.sleep(15)
        if not win_endpoint_id:
            print("  ⚠️  Windows agent not registered — running SSM-only verification")
```

Then after the SSM `winharden` block, add:
```python
        # If Windows endpoint registered, also dispatch CRs for full stack verification
        if win_endpoint_id:
            if "win_patch" in phases:
                print("\n  [win_patch via CR]")
                client.run_cr(
                    "[Phase win_patch-aws-win] Windows patch management",
                    "agent_win_patch", win_endpoint_id, {"dry_run": True},
                )
                log("win_patch CR dispatched via endpoint asset")
            if "winharden" in phases:
                print("\n  [winharden via CR]")
                client.run_cr(
                    "[Phase winharden-aws-win] Windows hardening",
                    "agent_winharden", win_endpoint_id, {"dry_run": True},
                )
                log("winharden CR dispatched via endpoint asset")
```

- [ ] **Step 6: Verify syntax**

```bash
docker exec nexplane-backend-1 python -c "import ast; ast.parse(open('tests/smoke/test_agent_live.py').read()); print('syntax OK')"
```

- [ ] **Step 7: Verify stub tracks still work**

```bash
docker exec nexplane-backend-1 python tests/smoke/test_agent_live.py \
  --base-url http://localhost:8000 --email admin@acme.example --password admin123 \
  --cloud gcp --os linux --phases linux_patch \
  --gcp-project nexplane 2>&1 | tail -5
```

Expected: `✅ ALL SELECTED TRACKS PASSED`

- [ ] **Step 8: Run backend tests**

```bash
docker exec nexplane-backend-1 python -m pytest tests/ -q 2>&1 | tail -5
```

- [ ] **Step 9: Commit**

```bash
git add backend/tests/smoke/test_agent_live.py
git commit -m "feat(smoke): add CR-based agent dispatch to test_agent_live.py — uses endpoint asset CRs when agent registers, SSM fallback"
```
