# CR Manifest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose a `GET /cr-manifest` endpoint and `get_cr_manifest()` MCP tool that return the full Nexplane CR type vocabulary enriched with inferred planning metadata (domain, action_class, touches, preconditions, effects, rollback_type) in one shot.

**Architecture:** A `ManifestBuilder` service walks all JSON CR definition files at startup and caches an enriched list in memory. A router exposes it at `GET /cr-manifest` with optional filters. The MCP tool wraps the same builder with the same filters. No database access — entirely derived from the JSON definition files.

**Tech Stack:** Python/FastAPI, pytest, existing MCP tool pattern (`@mcp.tool()`), existing router pattern (`APIRouter`)

---

## File Map

| File | Status | Change |
|------|--------|--------|
| `backend/app/services/manifest_builder.py` | Create | Inference pipeline + in-memory cache |
| `backend/app/routers/cr_manifest.py` | Create | `GET /cr-manifest` with filter params |
| `backend/app/main.py` | Modify | Import + register router; build manifest at startup |
| `backend/app/mcp_tools/change_requests.py` | Modify | Add `get_cr_manifest()` MCP tool |
| `backend/tests/unit/test_manifest_builder.py` | Create | Unit tests for all inference functions |
| `backend/tests/smoke/test_aws_live.py` | Modify | Add `CR_MANIFEST` smoke phase |

---

## Task 1: ManifestBuilder service

**Files:**
- Create: `backend/app/services/manifest_builder.py`
- Create: `backend/tests/unit/test_manifest_builder.py`

**Context:** The builder walks every `.json` file in `backend/app/connectors/change_type_definitions/`, runs inference over each, and stores the enriched list in a module-level variable. Consumers call `get_manifest(domain=None, action_class=None, touches=None, rollback_type=None)` to get filtered results. The catalog_service (already used in `mcp_tools/change_requests.py`) loads the same JSON files — we read them directly from disk here instead of going through the catalog because we need the raw dict, not the catalog object.

- [ ] **Step 1: Write the failing tests first**

Create `backend/tests/unit/test_manifest_builder.py`:

```python
"""Unit tests for CR manifest inference pipeline."""
import pytest
from app.services.manifest_builder import (
    build_manifest,
    get_manifest,
    _infer_domain,
    _infer_action_class,
    _infer_touches,
    _infer_rollback_type,
    _infer_preconditions,
    _infer_effects,
)


# ---- Minimal CR definition fixtures ----

def _defn(change_type, rollback_action=None, parameters=None, preflight_checks=None):
    d = {
        "change_type": change_type,
        "display_name": change_type.replace("_", " ").title(),
        "steps": [{"generic_action": change_type, "purpose": "execute", "required": True}],
        "preflight_checks": preflight_checks or ["connector_reachable"],
        "verification_methods": ["api_check"],
    }
    if rollback_action:
        d["rollback_action"] = rollback_action
        d["rollback_connector_type"] = "nexplane_agent"
    if parameters:
        d["parameters"] = parameters
    return d


# ---- Domain inference ----

def test_infer_domain_ec2():
    assert _infer_domain("ec2_stop") == "aws_compute"

def test_infer_domain_iam():
    assert _infer_domain("iam_user_create") == "aws_identity"

def test_infer_domain_attach_iam():
    assert _infer_domain("attach_iam_policy") == "aws_identity"

def test_infer_domain_rotate():
    assert _infer_domain("rotate_iam_key") == "credential_rotation"

def test_infer_domain_configure_selinux():
    assert _infer_domain("configure_selinux") == "hardening"

def test_infer_domain_configure_seccomp():
    assert _infer_domain("configure_seccomp") == "hardening"

def test_infer_domain_selinux_learn():
    assert _infer_domain("selinux_learn") == "hardening"

def test_infer_domain_isolate():
    assert _infer_domain("isolate_host") == "incident_response"

def test_infer_domain_patch():
    assert _infer_domain("patch_campaign") == "compliance"

def test_infer_domain_gcp():
    assert _infer_domain("gcp_firewall_create") == "gcp"

def test_infer_domain_gce():
    assert _infer_domain("gce_instance_create") == "gcp"

def test_infer_domain_azure():
    assert _infer_domain("azure_vm_create") == "azure"

def test_infer_domain_fallback():
    assert _infer_domain("remote_command") == "infrastructure"


# ---- Action class inference ----

def test_infer_action_class_create():
    assert _infer_action_class("ec2_launch") == "create"

def test_infer_action_class_delete():
    assert _infer_action_class("ec2_terminate") == "delete"

def test_infer_action_class_configure():
    assert _infer_action_class("configure_selinux") == "configure"

def test_infer_action_class_rotate():
    assert _infer_action_class("rotate_iam_key") == "rotate"

def test_infer_action_class_patch():
    assert _infer_action_class("patch_campaign") == "patch"

def test_infer_action_class_scan():
    assert _infer_action_class("trivy_scan") == "scan"

def test_infer_action_class_audit():
    assert _infer_action_class("authorized_keys_audit") == "audit"

def test_infer_action_class_fallback():
    assert _infer_action_class("remote_command") == "execute"


# ---- Rollback type inference ----

def test_infer_rollback_reversible():
    defn = _defn("configure_selinux", rollback_action="configure_selinux")
    assert _infer_rollback_type(defn) == "reversible"

def test_infer_rollback_permanent():
    defn = _defn("rotate_iam_key")
    assert _infer_rollback_type(defn) == "permanent"

def test_infer_rollback_snapshot_based():
    defn = _defn("rds_instance_delete", rollback_action="restore_rds_snapshot")
    assert _infer_rollback_type(defn) == "snapshot_based"


# ---- Precondition inference ----

def test_infer_preconditions_service_name():
    defn = _defn("configure_selinux", parameters={
        "service_name": {"type": "string", "required": True}
    })
    preconditions = _infer_preconditions(defn)
    assert "target service identified" in preconditions

def test_infer_preconditions_asset_exists():
    defn = _defn("configure_selinux", preflight_checks=["connector_reachable", "asset_exists"])
    preconditions = _infer_preconditions(defn)
    assert "target asset reachable" in preconditions

def test_infer_preconditions_connector():
    defn = _defn("configure_selinux", preflight_checks=["connector_reachable"])
    preconditions = _infer_preconditions(defn)
    assert "connector credential available" in preconditions

def test_infer_preconditions_unknown_required_param():
    defn = _defn("something", parameters={
        "widget_id": {"type": "string", "required": True}
    })
    preconditions = _infer_preconditions(defn)
    assert "widget_id provided" in preconditions


# ---- Effects inference ----

def test_infer_effects_configure_reversible():
    defn = _defn("configure_selinux", rollback_action="configure_selinux")
    effects = _infer_effects(defn)
    assert any("reversible" in e for e in effects)

def test_infer_effects_rotate():
    defn = _defn("rotate_iam_key")
    effects = _infer_effects(defn)
    assert any("replaced" in e or "rotated" in e for e in effects)

def test_infer_effects_delete_permanent():
    defn = _defn("ec2_terminate")
    effects = _infer_effects(defn)
    assert any("permanent" in e or "deleted" in e or "terminated" in e for e in effects)


# ---- Full manifest build ----

def test_full_manifest_count():
    entries = build_manifest()
    assert len(entries) >= 390

def test_manifest_entry_has_required_fields():
    entries = build_manifest()
    required = {"change_type", "display_name", "domain", "action_class",
                "touches", "preconditions", "effects", "rollback_type"}
    for entry in entries[:10]:
        missing = required - entry.keys()
        assert not missing, f"{entry['change_type']} missing {missing}"

def test_get_manifest_filter_domain():
    entries = get_manifest(domain="hardening")
    assert all(e["domain"] == "hardening" for e in entries)
    types = {e["change_type"] for e in entries}
    assert "configure_selinux" in types
    assert "configure_seccomp" in types

def test_get_manifest_filter_action_class():
    entries = get_manifest(action_class="rotate")
    assert all(e["action_class"] == "rotate" for e in entries)
    types = {e["change_type"] for e in entries}
    assert "rotate_iam_key" in types

def test_get_manifest_filter_rollback_type():
    entries = get_manifest(rollback_type="permanent")
    assert all(e["rollback_type"] == "permanent" for e in entries)

def test_get_manifest_filter_combined():
    entries = get_manifest(domain="hardening", rollback_type="reversible")
    assert all(e["domain"] == "hardening" and e["rollback_type"] == "reversible" for e in entries)
    assert len(entries) > 0
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "docker exec nexplane-backend-1 python -m pytest tests/unit/test_manifest_builder.py -v 2>&1 | tail -10"
```

Expected: `ModuleNotFoundError: No module named 'app.services.manifest_builder'`

- [ ] **Step 3: Create `backend/app/services/manifest_builder.py`**

```python
"""CR Manifest Builder — infers planning metadata from change type JSON definitions."""
from __future__ import annotations

import json
import pathlib
from typing import Optional

_DEFINITIONS_DIR = pathlib.Path(__file__).parent.parent / "connectors" / "change_type_definitions"

# Module-level cache — built once at startup via build_manifest(), reused by get_manifest()
_MANIFEST: list[dict] | None = None


# ---------------------------------------------------------------------------
# Domain inference
# ---------------------------------------------------------------------------

_DOMAIN_PREFIXES: list[tuple[list[str], str]] = [
    (["iam_", "attach_iam", "detach_iam", "disable_iam", "enable_iam"], "aws_identity"),
    (["ec2_", "rds_", "s3_", "route53_", "alb_", "elb_", "eks_", "ecr_",
      "cloudwatch_", "cloudfront_", "elasticache_", "lambda_", "sqs_", "sns_",
      "key_pair_", "ebs_"], "aws_compute"),
    (["gce_", "gcp_"], "gcp"),
    (["azure_ad_", "azure_"], "azure"),
    (["oci_"], "oci"),
    (["rotate_", "revoke_", "credential_"], "credential_rotation"),
    (["configure_selinux", "configure_seccomp", "configure_apparmor",
      "configure_windows", "deploy_applocker", "wdac_", "asr_", "sysmon_",
      "selinux_learn", "apparmor_learn", "seccomp_learn",
      "apply_protocol_control", "disable_kernel_feature", "apply_registry_fix",
      "remove_vulnerable_package"], "hardening"),
    (["isolate_", "lockdown_", "phishing_", "preserve_evidence"], "incident_response"),
    (["patch_", "enforce_cis", "trivy_", "lynis_", "openscap_",
      "authorized_keys_", "sudoers_", "collect_evidence", "revoke_exposed_"], "compliance"),
    (["offboard_", "onboard_", "emergency_user_", "ldap_", "okta_",
      "keycloak_", "gitlab_", "enforce_mfa"], "identity"),
    (["terraform_", "ansible_", "helm_"], "iac"),
    (["backup_", "create_backup", "verify_backup", "restore_", "dr_"], "backup_dr"),
    (["microsegmentation_", "security_group_", "dns_", "remote_command",
      "deploy_nexplane_", "tailscale_"], "networking"),
]


def _infer_domain(change_type: str) -> str:
    for prefixes, domain in _DOMAIN_PREFIXES:
        for prefix in prefixes:
            if change_type.startswith(prefix) or change_type == prefix.rstrip("_"):
                return domain
    return "infrastructure"


# ---------------------------------------------------------------------------
# Action class inference
# ---------------------------------------------------------------------------

_ACTION_TOKENS: list[tuple[list[str], str]] = [
    (["create", "launch", "provision", "deploy", "onboard", "add"], "create"),
    (["delete", "terminate", "destroy", "remove", "offboard", "revoke"], "delete"),
    (["configure", "apply", "enforce", "set", "enable", "disable", "update",
      "attach", "detach", "block", "restore"], "configure"),
    (["rotate", "rotate_"], "rotate"),
    (["patch", "patch_"], "patch"),
    (["scan", "trivy", "openscap", "lynis"], "scan"),
    (["audit", "authorized_keys", "sudoers"], "audit"),
    (["isolate", "lockdown", "phishing", "preserve"], "isolate"),
    (["learn"], "observe"),
    (["verify", "collect"], "verify"),
    (["rollback"], "rollback"),
]


def _infer_action_class(change_type: str) -> str:
    tokens = change_type.split("_")
    first = tokens[0] if tokens else ""
    last = tokens[-1] if tokens else ""
    for keywords, action_class in _ACTION_TOKENS:
        for kw in keywords:
            if first == kw or last == kw or change_type.startswith(kw):
                return action_class
    return "execute"


# ---------------------------------------------------------------------------
# Touches inference
# ---------------------------------------------------------------------------

_TOUCHES_PREFIXES: list[tuple[list[str], list[str]]] = [
    (["ec2_"], ["ec2_instance"]),
    (["rds_"], ["rds_instance"]),
    (["s3_", "block_s3_"], ["s3_bucket"]),
    (["iam_user", "disable_iam_user", "enable_iam_user"], ["iam_user"]),
    (["iam_", "attach_iam", "detach_iam"], ["iam_policy"]),
    (["rotate_iam_key", "rotate_iam"], ["iam_user", "iam_access_key"]),
    (["rotate_ssh", "rotate_ssh_keys"], ["ssh_key", "linux_host"]),
    (["rotate_db_", "rotate_postgres_", "rotate_redis_"], ["database_credential"]),
    (["rotate_vault_", "rotate_secrets_manager_", "rotate_jwt_"], ["secret"]),
    (["configure_selinux", "selinux_learn"], ["selinux_policy", "linux_host"]),
    (["configure_seccomp", "seccomp_learn"], ["seccomp_profile", "linux_host"]),
    (["configure_apparmor", "apparmor_learn"], ["apparmor_profile", "linux_host"]),
    (["configure_windows_firewall", "deploy_applocker", "wdac_", "asr_", "sysmon_"], ["windows_host"]),
    (["azure_vm_"], ["azure_vm"]),
    (["azure_nsg", "azure_update_nsg", "azure_restore_nsg"], ["azure_nsg"]),
    (["azure_storage", "azure_blob", "azure_rotate_storage"], ["azure_storage"]),
    (["azure_sql_"], ["azure_sql"]),
    (["gce_", "gcp_"], ["gcp_compute"]),
    (["gcp_firewall_"], ["gcp_firewall"]),
    (["gcp_rotate_service_account", "gcp_disable_service_account"], ["gcp_service_account"]),
    (["oci_"], ["oci_resource"]),
    (["eks_", "ecr_", "helm_"], ["kubernetes"]),
    (["patch_", "remove_vulnerable_package"], ["linux_host", "package"]),
    (["isolate_host", "lockdown_"], ["host", "network"]),
    (["offboard_", "onboard_", "ldap_", "okta_", "keycloak_", "gitlab_"], ["user_account"]),
    (["dns_", "route53_"], ["dns_record"]),
    (["security_group_"], ["security_group"]),
    (["backup_", "create_backup", "verify_backup"], ["backup_artifact"]),
    (["restore_"], ["backup_artifact", "target_resource"]),
    (["microsegmentation_"], ["network_policy"]),
    (["terraform_"], ["infrastructure"]),
    (["ansible_"], ["host"]),
]


def _infer_touches(change_type: str) -> list[str]:
    for prefixes, touches in _TOUCHES_PREFIXES:
        for prefix in prefixes:
            if change_type.startswith(prefix) or change_type == prefix.rstrip("_"):
                return touches
    return ["resource"]


# ---------------------------------------------------------------------------
# Rollback type inference
# ---------------------------------------------------------------------------

def _infer_rollback_type(defn: dict) -> str:
    rollback = defn.get("rollback_action", "")
    if not rollback:
        return "permanent"
    if any(w in rollback for w in ("restore", "snapshot", "backup")):
        return "snapshot_based"
    return "reversible"


# ---------------------------------------------------------------------------
# Preconditions inference
# ---------------------------------------------------------------------------

_PARAM_PRECONDITION_MAP: dict[str, str] = {
    "service_name": "target service identified",
    "instance_id": "target instance identified",
    "cve_id": "CVE ID known",
    "package_name": "target package name known",
    "user_id": "target user identified",
    "username": "target user identified",
    "key_id": "access key ID known",
    "module_source": "SELinux policy module source available",
    "profile_content": "AppArmor profile content available",
    "bucket_name": "target bucket name known",
    "cluster_name": "target cluster name known",
    "secret_id": "secret ID known",
    "vault_path": "Vault secret path known",
    "zone_name": "DNS zone name known",
    "resource_group": "Azure resource group identified",
    "subscription_id": "Azure subscription identified",
    "project_id": "GCP project identified",
    "compartment_id": "OCI compartment identified",
    "playbook": "Ansible playbook path known",
    "template_body": "CloudFormation template available",
    "module_path": "Terraform module path known",
}


def _infer_preconditions(defn: dict) -> list[str]:
    preconditions: list[str] = []
    preflight = defn.get("preflight_checks", [])
    if "asset_exists" in preflight:
        preconditions.append("target asset reachable")
    if "connector_reachable" in preflight:
        preconditions.append("connector credential available")
    params = defn.get("parameters", {})
    for param_name, param_meta in params.items():
        if not param_meta.get("required", False):
            continue
        mapped = _PARAM_PRECONDITION_MAP.get(param_name)
        if mapped:
            preconditions.append(mapped)
        else:
            preconditions.append(f"{param_name} provided")
    return preconditions if preconditions else ["target resource identified"]


# ---------------------------------------------------------------------------
# Effects inference
# ---------------------------------------------------------------------------

def _infer_effects(defn: dict) -> list[str]:
    change_type = defn.get("change_type", "")
    action_class = _infer_action_class(change_type)
    rollback_type = _infer_rollback_type(defn)
    display = defn.get("display_name", change_type.replace("_", " "))

    suffix = " (reversible)" if rollback_type == "reversible" else \
             " (snapshot-based recovery)" if rollback_type == "snapshot_based" else \
             " (permanent)"

    if action_class == "create":
        return [f"{display} created{suffix}"]
    if action_class == "delete":
        return [f"{display} deleted (permanent)"]
    if action_class == "configure":
        return [f"configuration applied{suffix}"]
    if action_class == "rotate":
        return ["credential replaced — prior credential invalidated (permanent)"]
    if action_class == "patch":
        return ["vulnerable packages updated on affected hosts (permanent)"]
    if action_class == "scan":
        return ["vulnerability findings recorded (read-only, no system change)"]
    if action_class == "audit":
        return ["audit findings recorded (read-only, no system change)"]
    if action_class == "isolate":
        return [f"host isolated from network{suffix}"]
    if action_class == "observe":
        return ["observations collected (no persistent state created)"]
    if action_class == "verify":
        return ["verification results recorded (read-only)"]
    return [f"{display} executed{suffix}"]


# ---------------------------------------------------------------------------
# Full entry builder
# ---------------------------------------------------------------------------

def _build_entry(defn: dict) -> dict:
    change_type = defn.get("change_type", "")
    entry = {
        "change_type": change_type,
        "display_name": defn.get("display_name", ""),
        "description": defn.get("description", ""),
        "domain": _infer_domain(change_type),
        "action_class": _infer_action_class(change_type),
        "touches": _infer_touches(change_type),
        "preconditions": _infer_preconditions(defn),
        "effects": _infer_effects(defn),
        "rollback_type": _infer_rollback_type(defn),
        "parameters": defn.get("parameters", {}),
    }
    return entry


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_manifest() -> list[dict]:
    """Walk all CR definition files and return the enriched manifest list.

    Results are cached in _MANIFEST after first call.
    """
    global _MANIFEST
    entries = []
    for path in sorted(_DEFINITIONS_DIR.glob("**/*.json")):
        try:
            defn = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(defn, dict) and "change_type" in defn:
                entries.append(_build_entry(defn))
        except Exception:
            continue
    _MANIFEST = entries
    return entries


def get_manifest(
    domain: Optional[str] = None,
    action_class: Optional[str] = None,
    touches: Optional[str] = None,
    rollback_type: Optional[str] = None,
) -> list[dict]:
    """Return filtered manifest entries. Builds manifest on first call if not cached."""
    global _MANIFEST
    if _MANIFEST is None:
        build_manifest()
    entries = _MANIFEST
    if domain:
        entries = [e for e in entries if e["domain"] == domain]
    if action_class:
        entries = [e for e in entries if e["action_class"] == action_class]
    if touches:
        entries = [e for e in entries if touches in e["touches"]]
    if rollback_type:
        entries = [e for e in entries if e["rollback_type"] == rollback_type]
    return entries
```

- [ ] **Step 4: SCP both files to EC2**

```bash
scp -i ~/.ssh/id_ed25519 \
  backend/app/services/manifest_builder.py \
  ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/services/manifest_builder.py

scp -i ~/.ssh/id_ed25519 \
  backend/tests/unit/test_manifest_builder.py \
  ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/tests/unit/test_manifest_builder.py
```

- [ ] **Step 5: Run tests**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "docker exec nexplane-backend-1 python -m pytest tests/unit/test_manifest_builder.py -v 2>&1 | tail -30"
```

Expected: All tests pass. If `test_full_manifest_count` fails with count < 390, check that `_DEFINITIONS_DIR` resolves correctly inside the container — the path is relative to `manifest_builder.py`'s location.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/manifest_builder.py backend/tests/unit/test_manifest_builder.py
git commit -m "feat(manifest): add ManifestBuilder service — infer domain/action_class/touches/preconditions/effects from CR definitions"
```

---

## Task 2: REST endpoint

**Files:**
- Create: `backend/app/routers/cr_manifest.py`
- Modify: `backend/app/main.py`

**Context:** Follow the same pattern as other routers. No database access — just calls `get_manifest()`. Register the router in `main.py` alongside the others (line ~173). The router prefix is `/cr-manifest`.

- [ ] **Step 1: Create `backend/app/routers/cr_manifest.py`**

```python
from typing import Optional
from fastapi import APIRouter, Depends
from app.auth import current_user
from app.services.manifest_builder import get_manifest

router = APIRouter(prefix="/cr-manifest", tags=["cr-manifest"])


@router.get("")
async def list_cr_manifest(
    domain: Optional[str] = None,
    action_class: Optional[str] = None,
    touches: Optional[str] = None,
    rollback_type: Optional[str] = None,
    user=Depends(current_user),
):
    entries = get_manifest(
        domain=domain,
        action_class=action_class,
        touches=touches,
        rollback_type=rollback_type,
    )
    return {"count": len(entries), "entries": entries}
```

- [ ] **Step 2: Register in `backend/app/main.py`**

Find the import block at the top of `main.py`. Read the file to find the exact import style used for other routers — it varies. Add after the last `from app.routers import ...` import:

```python
from app.routers import cr_manifest as cr_manifest_router
```

Then find the line `app.include_router(security_policy_router.router)` (the last `include_router` call) and add immediately after:

```python
app.include_router(cr_manifest_router.router)
```

Also add to the `lifespan` function after `init_catalog_service(...)`:

```python
    from app.services.manifest_builder import build_manifest
    build_manifest()
```

- [ ] **Step 3: SCP both files to EC2**

```bash
scp -i ~/.ssh/id_ed25519 \
  backend/app/routers/cr_manifest.py \
  ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/routers/cr_manifest.py

scp -i ~/.ssh/id_ed25519 \
  backend/app/main.py \
  ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/main.py
```

- [ ] **Step 4: Restart backend and verify endpoint**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "docker restart nexplane-backend-1 && sleep 8 && \
   docker exec nexplane-backend-1 curl -sf \
   'http://localhost:8000/cr-manifest' \
   -H 'Authorization: Bearer \$(cat /tmp/nexplane_token 2>/dev/null || echo test)' \
   | python3 -c 'import sys,json; d=json.load(sys.stdin); print(\"count:\", d[\"count\"])'"
```

If the token isn't cached at `/tmp/nexplane_token`, get one first:

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "docker exec nexplane-backend-1 python -c \
  \"import asyncio; from app.database import AsyncSessionLocal; from sqlalchemy import text; \
  async def f():
      async with AsyncSessionLocal() as db:
          r = await db.execute(text(\\\"SELECT token FROM api_tokens LIMIT 1\\\"))
          print(r.scalar())
  asyncio.run(f())\""
```

Then test the endpoint:

```bash
TOKEN=<token from above>
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "docker exec nexplane-backend-1 curl -sf \
   'http://localhost:8000/cr-manifest?domain=hardening' \
   -H 'Authorization: Bearer $TOKEN' \
   | python3 -c 'import sys,json; d=json.load(sys.stdin); print(\"count:\",d[\"count\"]); print([e[\"change_type\"] for e in d[\"entries\"][:5]])'"
```

Expected: count > 0, `configure_selinux` in the list.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/cr_manifest.py backend/app/main.py
git commit -m "feat(manifest): add GET /cr-manifest endpoint with domain/action_class/touches/rollback_type filters"
```

---

## Task 3: MCP tool

**Files:**
- Modify: `backend/app/mcp_tools/change_requests.py`

**Context:** Read the existing MCP tools at the bottom of `change_requests.py` to find where to append. The `get_cr_manifest` tool authenticates via the existing `_auth(token)` helper but doesn't need the database — it calls `get_manifest()` directly. Still call `_auth` to enforce token validation.

- [ ] **Step 1: Read the end of `backend/app/mcp_tools/change_requests.py`** to find where the last `@mcp.tool()` ends, so you know where to append.

- [ ] **Step 2: Append `get_cr_manifest` tool to `backend/app/mcp_tools/change_requests.py`**

Add at the end of the file:

```python
@mcp.tool()
async def get_cr_manifest(
    token: str,
    domain: Optional[str] = None,
    action_class: Optional[str] = None,
    touches: Optional[str] = None,
    rollback_type: Optional[str] = None,
) -> dict[str, Any]:
    """
    Return the full CR type vocabulary enriched with planning metadata.

    Each entry includes: change_type, display_name, domain, action_class, touches,
    preconditions, effects, rollback_type, and parameters.

    Use filters to load only the relevant slice for a planning goal:
    - domain: hardening | credential_rotation | incident_response | compliance |
               identity | aws_compute | aws_identity | gcp | azure | oci |
               iac | backup_dr | networking | infrastructure
    - action_class: create | delete | configure | rotate | patch | scan |
                    audit | isolate | observe | verify | execute
    - touches: ec2_instance | linux_host | iam_user | selinux_policy | ...
    - rollback_type: reversible | permanent | snapshot_based

    Returns {"count": N, "entries": [...]}
    """
    from app.services.manifest_builder import get_manifest
    user, db, db_cm = await _auth(token)
    try:
        entries = get_manifest(
            domain=domain,
            action_class=action_class,
            touches=touches,
            rollback_type=rollback_type,
        )
        return {"count": len(entries), "entries": entries}
    except Exception as exc:
        return {"error": str(exc)}
    finally:
        await db_cm.__aexit__(None, None, None)
```

Note: `Optional` is already imported at the top of the file. If it isn't, add `from typing import Optional` to the imports.

- [ ] **Step 3: SCP to EC2**

```bash
scp -i ~/.ssh/id_ed25519 \
  backend/app/mcp_tools/change_requests.py \
  ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/mcp_tools/change_requests.py
```

- [ ] **Step 4: Restart backend and verify MCP tool registered**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "docker restart nexplane-backend-1 && sleep 8 && \
   docker exec nexplane-backend-1 python -c \
   \"from app.mcp_server import mcp; \
   tools = [t.name for t in mcp.list_tools() if 'manifest' in t.name]; \
   print(tools)\""
```

Expected: `['get_cr_manifest']`

- [ ] **Step 5: Commit**

```bash
git add backend/app/mcp_tools/change_requests.py
git commit -m "feat(manifest): add get_cr_manifest() MCP tool with domain/action_class/touches/rollback_type filters"
```

---

## Task 4: CR_MANIFEST smoke phase

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py`

**Context:** This smoke phase doesn't need a live EC2 instance — it tests the backend API directly. It runs against the existing platform. Read the smoke test file and find `def run_phase_cr_manifest` if it exists, otherwise find `def run_phase_apparmor_autogen` to understand where to insert the new function. Wire it into `main()` the same way other phases are wired.

- [ ] **Step 1: Read relevant section of `backend/tests/smoke/test_aws_live.py`**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "docker exec nexplane-backend-1 grep -n 'def run_phase\|if.*in phases' \
   /app/tests/smoke/test_aws_live.py | tail -20"
```

Note the last phase function and the `main()` wiring pattern.

- [ ] **Step 2: Add `run_phase_cr_manifest` function**

Find the last `def run_phase_*` function in the file and add `run_phase_cr_manifest` immediately after it:

```python
def run_phase_cr_manifest(client, base_url, **kwargs):
    import time as _time

    log = lambda msg: print(f"  [CR_MANIFEST] {msg}", flush=True)
    log("Starting CR_MANIFEST smoke phase")

    # ---- 1. Full manifest ----
    manifest = client.get("/cr-manifest")
    assert "count" in manifest and "entries" in manifest, f"Unexpected response shape: {manifest}"
    assert manifest["count"] >= 390, f"Expected >= 390 entries, got {manifest['count']}"
    log(f"Full manifest: {manifest['count']} entries ✓")

    # ---- 2. Schema check on first 20 entries ----
    required_fields = {"change_type", "display_name", "domain", "action_class",
                       "touches", "preconditions", "effects", "rollback_type"}
    for entry in manifest["entries"][:20]:
        missing = required_fields - entry.keys()
        assert not missing, f"{entry.get('change_type')} missing fields: {missing}"
    log("Schema check: required fields present on sampled entries ✓")

    # ---- 3. Filter by domain=hardening ----
    hardening = client.get("/cr-manifest", params={"domain": "hardening"})
    assert hardening["count"] > 0, "domain=hardening returned 0 entries"
    types_hardening = {e["change_type"] for e in hardening["entries"]}
    for expected in ("configure_selinux", "configure_seccomp", "configure_apparmor"):
        assert expected in types_hardening, f"{expected} not in hardening domain"
    assert all(e["domain"] == "hardening" for e in hardening["entries"]), \
        "domain filter returned non-hardening entries"
    log(f"domain=hardening: {hardening['count']} entries, configure_selinux/seccomp/apparmor present ✓")

    # ---- 4. Filter by action_class=rotate ----
    rotations = client.get("/cr-manifest", params={"action_class": "rotate"})
    assert rotations["count"] > 0, "action_class=rotate returned 0 entries"
    types_rotate = {e["change_type"] for e in rotations["entries"]}
    assert "rotate_iam_key" in types_rotate, "rotate_iam_key not in action_class=rotate"
    assert all(e["action_class"] == "rotate" for e in rotations["entries"]), \
        "action_class filter returned non-rotate entries"
    log(f"action_class=rotate: {rotations['count']} entries ✓")

    # ---- 5. Filter by rollback_type=permanent ----
    permanent = client.get("/cr-manifest", params={"rollback_type": "permanent"})
    assert permanent["count"] > 0, "rollback_type=permanent returned 0 entries"
    assert all(e["rollback_type"] == "permanent" for e in permanent["entries"]), \
        "rollback_type filter returned non-permanent entries"
    log(f"rollback_type=permanent: {permanent['count']} entries ✓")

    # ---- 6. Combined filter ----
    combined = client.get("/cr-manifest", params={"domain": "hardening", "rollback_type": "reversible"})
    assert combined["count"] > 0, "combined filter returned 0 entries"
    assert all(e["domain"] == "hardening" and e["rollback_type"] == "reversible"
               for e in combined["entries"]), "combined filter mismatch"
    log(f"Combined domain=hardening+rollback_type=reversible: {combined['count']} entries ✓")

    # ---- 7. touches filter ----
    linux_hosts = client.get("/cr-manifest", params={"touches": "linux_host"})
    assert linux_hosts["count"] > 0, "touches=linux_host returned 0 entries"
    assert all("linux_host" in e["touches"] for e in linux_hosts["entries"]), \
        "touches filter returned entries without linux_host"
    log(f"touches=linux_host: {linux_hosts['count']} entries ✓")

    log("CR_MANIFEST PASSED ✓")
    return {"status": "passed", "manifest_count": manifest["count"]}
```

- [ ] **Step 3: Wire into `main()`**

Find the last `if "..." in phases:` block in `main()` and add immediately after:

```python
        if "CR_MANIFEST" in phases:
            run_phase_cr_manifest(client, base_url=args.base_url)
```

- [ ] **Step 4: Commit + push + pull on EC2**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "feat(smoke): add CR_MANIFEST phase — validates manifest count, schema, and all filter combinations"
git push origin master
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "cd /home/ec2-user/nexplane && git pull && \
   docker restart nexplane-backend-1 && sleep 8 && \
   docker exec nexplane-backend-1 curl -sf http://localhost:8000/health"
```

- [ ] **Step 5: Run the CR_MANIFEST smoke phase**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "nohup docker exec nexplane-backend-1 stdbuf -oL python -u \
   /app/tests/smoke/test_aws_live.py --phases CR_MANIFEST \
   > /tmp/cr_manifest_smoke.log 2>&1 & echo started"
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "tail -f /tmp/cr_manifest_smoke.log"
```

Expected:
```
  [CR_MANIFEST] Full manifest: 39X entries ✓
  [CR_MANIFEST] Schema check: required fields present on sampled entries ✓
  [CR_MANIFEST] domain=hardening: N entries, configure_selinux/seccomp/apparmor present ✓
  [CR_MANIFEST] action_class=rotate: N entries ✓
  [CR_MANIFEST] rollback_type=permanent: N entries ✓
  [CR_MANIFEST] Combined domain=hardening+rollback_type=reversible: N entries ✓
  [CR_MANIFEST] touches=linux_host: N entries ✓
  [CR_MANIFEST] CR_MANIFEST PASSED ✓

============================================================
✅ ALL SELECTED PHASES PASSED
============================================================
```

- [ ] **Step 6: Fix any failures and re-run**

Common issues:
- Auth error on `/cr-manifest`: check that `current_user` dependency is wired correctly in the router — compare with `backup.py`
- Count below 390: confirm `_DEFINITIONS_DIR` path resolves inside the Docker container (the container mounts at `/app`, not `/home/ec2-user/nexplane/backend`); use `pathlib.Path(__file__).parent.parent / "connectors" / "change_type_definitions"` which resolves relative to the installed file location
- A filter returns wrong entries: check inference function for that field

- [ ] **Step 7: Final commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "feat(smoke): CR_MANIFEST phase passing"
git push origin master
```
