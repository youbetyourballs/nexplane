# CR Manifest Design

**Date:** 2026-05-31
**Status:** Approved

---

## Goal

Expose the full Nexplane CR type vocabulary as a single, enriched, machine-readable document — the CR manifest — so that LLM planning agents can load the complete action space in one request and reason about goal-directed security operations constrained to platform primitives.

---

## Background

Nexplane has 430+ CR types across every cloud, OS, and identity system. Today there is no way to express a security goal ("harden this EC2 instance") and have the platform determine which CR types to apply in what order. The barrier is vocabulary: an LLM needs to know what actions exist, what each requires, what each changes, and whether it can be undone — before it can plan.

The existing `list_change_types` and `get_change_type` MCP tools return per-CR info on demand but are unsuitable for planning: 430+ individual calls is impractical, and the existing fields (steps, preflight_checks) don't capture planning-relevant semantics.

The CR manifest solves this by:
1. Serving the entire vocabulary in one request
2. Adding inferred planning metadata (preconditions, effects, touches, domain, action_class, rollback_type) to each entry
3. Supporting filters so a planning agent can load only the relevant slice

---

## Not In Scope

- The LLM planning agent that consumes the manifest (future feature)
- A UI for browsing the manifest
- Manual annotation overrides
- PDDL solver integration

---

## Architecture

### ManifestBuilder service

`backend/app/services/manifest_builder.py` — runs at backend startup, walks all JSON files in `connectors/change_type_definitions/`, runs inference over each entry, and caches the result in memory as a list of `ManifestEntry` dicts.

Because the manifest is derived entirely from the JSON definition files, it auto-updates whenever a new CR type is added — on next restart the new entry appears automatically. No manual manifest maintenance.

### Inference pipeline

Each CR definition passes through a chain of pure functions, each adding one field:

| Field | Inference source |
|-------|-----------------|
| `domain` | change_type name prefix (`ec2_*` → `aws_compute`, `iam_*` → `aws_identity`, `rotate_*` → `credential_rotation`, `configure_*` → `hardening`, `azure_*` → `azure`, `gcp_*` / `gce_*` → `gcp`, `isolate_*` / `lockdown_*` / `phishing_*` → `incident_response`, `patch_*` / `enforce_cis_*` / `*_audit` / `*_scan` → `compliance`, everything else → `infrastructure`) |
| `action_class` | First verb token of change_type: `create`, `delete`, `configure`, `rotate`, `scan`, `audit`, `isolate`, `patch`, `enforce`, `restore`, `deploy`, `attach`, `detach`, `update`, `enable`, `disable` |
| `touches` | Prefix + step generic_actions mapped to resource type strings (e.g. `ec2_stop` → `["ec2_instance"]`, `rotate_iam_key` → `["iam_user", "iam_access_key"]`, `configure_selinux` → `["selinux_policy", "linux_host"]`) |
| `preconditions` | Required parameters from `parameters` schema → natural-language strings; `asset_exists` in preflight_checks → "target asset reachable"; `connector_reachable` → "connector credential available" |
| `effects` | Derived from action_class + display_name + rollback presence: `configure` + rollback → "configuration changed (reversible)"; `delete` + no rollback → "resource permanently deleted"; `rotate` → "credential replaced" |
| `rollback_type` | `reversible` if `rollback_action` present; `permanent` if absent; `snapshot_based` if rollback_action contains "restore" or "snapshot" |

### REST endpoint

`GET /cr-manifest`

Optional query parameters (all combinable):
- `?domain=hardening`
- `?action_class=rotate`
- `?touches=ec2_instance`
- `?rollback_type=reversible`

Response: `{"count": N, "entries": [...]}`

Filtering is applied over the in-memory cache — no database query.

### MCP tool

`get_cr_manifest(domain?, action_class?, touches?, rollback_type?)` in `backend/app/mcp_tools/change_requests.py`

Same filter parameters as the REST endpoint. Returns the filtered entries. A planning agent loads just the domain slice it needs — e.g. `get_cr_manifest(domain="hardening")` for a host hardening goal — keeping context window usage manageable.

---

## Manifest Entry Schema

```json
{
  "change_type": "configure_selinux",
  "display_name": "Configure SELinux Policy Module",
  "description": "SELinux enforcement mode and policy module management.",
  "domain": "hardening",
  "action_class": "configure",
  "touches": ["selinux_policy", "linux_host"],
  "preconditions": [
    "target Linux host identified",
    "connector credential available"
  ],
  "effects": [
    "SELinux policy module installed (reversible)"
  ],
  "rollback_type": "reversible",
  "parameters": {
    "mode": {"type": "string", "required": false, "description": "enforcing | permissive"},
    "module_source": {"type": "string", "required": false, "description": ".te module source text"}
  }
}
```

A richer example with inferred preconditions from parameters:

```json
{
  "change_type": "patch_campaign",
  "display_name": "Emergency Patch Campaign",
  "description": "Fan out security patching across all hosts affected by a CVE.",
  "domain": "compliance",
  "action_class": "patch",
  "touches": ["linux_host", "package"],
  "preconditions": [
    "CVE ID or package name known",
    "target asset reachable",
    "connector credential available"
  ],
  "effects": [
    "vulnerable packages updated on affected hosts"
  ],
  "rollback_type": "permanent",
  "parameters": {
    "cve_id": {"type": "string", "required": false, "description": "e.g. CVE-2024-3094"},
    "package_name": {"type": "string", "required": false},
    "batch_size": {"type": "integer", "default": 10}
  }
}
```

---

## File Map

| File | Status | Change |
|------|--------|--------|
| `backend/app/services/manifest_builder.py` | Create | ManifestBuilder: inference pipeline + in-memory cache |
| `backend/app/routers/cr_manifest.py` | Create | `GET /cr-manifest` with filter params |
| `backend/app/main.py` | Modify | Register cr_manifest router + build manifest at startup |
| `backend/app/mcp_tools/change_requests.py` | Modify | Add `get_cr_manifest()` MCP tool |
| `backend/tests/unit/test_manifest_builder.py` | Create | Unit tests for inference functions |
| `backend/tests/smoke/test_aws_live.py` | Modify | Add `CR_MANIFEST` smoke phase |

---

## Inference Rules Detail

### Domain mapping (prefix priority order)

```python
DOMAIN_PREFIXES = [
    (["ec2_", "rds_", "s3_", "route53_", "alb_", "elb_", "eks_", "ecr_", "cloudwatch_", "cloudfront_", "elasticache_", "lambda_", "sqs_", "sns_", "key_pair_"], "aws_compute"),
    (["iam_", "attach_iam", "detach_iam", "disable_iam", "enable_iam"], "aws_identity"),
    (["gce_", "gcp_"], "gcp"),
    (["azure_", "azure_ad_"], "azure"),
    (["oci_"], "oci"),
    (["rotate_", "revoke_", "credential_"], "credential_rotation"),
    (["configure_selinux", "configure_seccomp", "configure_apparmor", "configure_windows", "deploy_applocker", "wdac_", "asr_", "sysmon_"], "hardening"),
    (["selinux_learn", "apparmor_learn", "seccomp_learn"], "hardening"),
    (["isolate_", "lockdown_", "phishing_", "preserve_evidence"], "incident_response"),
    (["patch_", "enforce_cis", "trivy_", "lynis_", "openscap_", "authorized_keys_", "sudoers_", "collect_evidence"], "compliance"),
    (["offboard_", "onboard_", "emergency_user_", "ldap_", "okta_", "keycloak_", "gitlab_", "enforce_mfa"], "identity"),
    (["terraform_", "ansible_", "helm_"], "iac"),
    (["backup_", "create_backup", "verify_backup", "restore_", "dr_"], "backup_dr"),
    (["microsegmentation_", "security_group_", "dns_", "remote_command"], "networking"),
]
# fallback: "infrastructure"
```

### Rollback type

```python
def infer_rollback_type(defn: dict) -> str:
    rollback = defn.get("rollback_action", "")
    if not rollback:
        return "permanent"
    if any(w in rollback for w in ("restore", "snapshot", "backup")):
        return "snapshot_based"
    return "reversible"
```

### Preconditions from parameters

Required parameters whose names contain known tokens are mapped to natural language:

```python
PARAM_PRECONDITION_MAP = {
    "service_name": "target service identified",
    "instance_id": "target EC2 instance identified",
    "cve_id": "CVE ID known",
    "package_name": "target package name known",
    "user_id": "target user identified",
    "username": "target user identified",
    "key_id": "access key ID known",
    "module_source": "SELinux policy module source available",
    "profile_content": "AppArmor profile content available",
}
```

Parameters not in the map that are required fall back to: `f"{param_name} provided"`.

---

## Testing

### Unit tests — `backend/tests/unit/test_manifest_builder.py`

- `test_infer_domain_ec2` — `ec2_stop` → `aws_compute`
- `test_infer_domain_rotate` — `rotate_iam_key` → `credential_rotation`
- `test_infer_domain_hardening` — `configure_selinux` → `hardening`
- `test_infer_domain_fallback` — unknown prefix → `infrastructure`
- `test_infer_action_class` — `patch_campaign` → `patch`
- `test_infer_rollback_reversible` — definition with `rollback_action` → `reversible`
- `test_infer_rollback_permanent` — definition without `rollback_action` → `permanent`
- `test_infer_rollback_snapshot` — `rollback_action: "restore_rds_snapshot"` → `snapshot_based`
- `test_infer_preconditions_from_params` — required `service_name` param → `"target service identified"` in preconditions
- `test_full_manifest_count` — full manifest has >= 390 entries
- `test_filter_by_domain` — `domain=hardening` returns only hardening entries
- `test_filter_by_action_class` — `action_class=rotate` returns only rotate entries
- `test_filter_combinable` — `domain=hardening&rollback_type=reversible` applies both filters

### Smoke phase — `CR_MANIFEST`

1. `GET /cr-manifest` → assert count >= 390, all entries have required fields
2. `GET /cr-manifest?domain=hardening` → assert `configure_selinux`, `configure_seccomp`, `configure_apparmor` present
3. `GET /cr-manifest?action_class=rotate` → assert `rotate_iam_key`, `rotate_ssh_keys` present
4. `GET /cr-manifest?rollback_type=permanent` → assert `rotate_iam_key` present (no rollback_action)
5. `GET /cr-manifest?domain=hardening&rollback_type=reversible` → combined filter works
6. MCP tool `get_cr_manifest(domain="credential_rotation")` returns matching entries
7. Assert every entry has: `change_type`, `domain`, `action_class`, `touches`, `preconditions`, `effects`, `rollback_type`

---

## Future: LLM Planning Agent Smoke Test

Once the manifest exists, a planning smoke test becomes the natural integration test for manifest quality:

1. Load `get_cr_manifest(domain="hardening")`
2. Provide goal: "Apply SELinux policy to nginx on asset X"
3. Assert the LLM output includes `configure_selinux` (or the soak/learn sequence leading to it)
4. Assert no hallucinated CR types outside the manifest appear in the plan

This smoke test is **not in scope** for this spec but the manifest schema is designed with it as the primary consumer.
