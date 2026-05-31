# AI Service Manifest Integration Design

**Date:** 2026-05-31
**Status:** Approved

---

## Goal

Replace the hardcoded `_CHANGE_TYPES_TEXT` constant in `ai_service.py` with a dynamic call to the CR manifest, giving the AI planning assistant rich planning metadata (domain grouping, preconditions, effects, rollback type, touches) for all 390 CR types instead of a flat list of ~80 bare names.

---

## Background

`ai_service.py` constructs a system prompt for the AI planning assistant that includes a `## Supported Change Types` section. Currently this is a hardcoded string (`_CHANGE_TYPES_TEXT`) listing ~80 CR type names grouped by rough category with no preconditions, effects, or rollback information. The LLM has no signal about what each CR requires, what it changes, or whether it can be undone.

The CR manifest (built by `manifest_builder.py`, served at `GET /cr-manifest`) enriches all 390 CR definitions with:
- `domain` — planning grouping (hardening, credential_rotation, incident_response, etc.)
- `action_class` — verb type (configure, rotate, patch, scan, etc.)
- `touches` — resource types affected
- `preconditions` — what must be known before using this CR
- `effects` — what the CR produces
- `rollback_type` — reversible / permanent / snapshot_based

Replacing the hardcoded constant with manifest data gives the LLM a complete, accurate, and self-maintaining vocabulary. When new CR types are added to the platform, they automatically appear in the AI planning context.

---

## Not In Scope

- Changes to proposal parsing (`_parse_proposal`)
- Changes to the `<nexplane-proposal>` format
- Changes to asset context formatting
- Proposal validation against the manifest (future)
- UI changes

---

## Architecture

### Change 1: `ai_service.py` — replace constant with dynamic formatter

Remove `_CHANGE_TYPES_TEXT`. Add `_build_change_types_text()` that calls `get_manifest()` and formats entries grouped by domain.

Format per entry (one line each):
```
<change_type>  — <display_name>  |  touches: <touches joined>  |  requires: <preconditions joined>  |  effects: <effects joined>  |  rollback: <rollback_type>
```

Grouped by domain:
```
## hardening
configure_selinux  — Configure SELinux Policy Module  |  touches: selinux_policy, linux_host  |  requires: connector credential available  |  rollback: reversible
configure_seccomp  — Configure Seccomp Profile  |  touches: seccomp_profile, linux_host  |  requires: connector credential available  |  rollback: reversible
selinux_learn  — SELinux Learn (Observe AVC Denials)  |  touches: selinux_policy, linux_host  |  requires: connector credential available  |  rollback: permanent

## credential_rotation
rotate_iam_key  — Rotate IAM Access Key  |  touches: iam_user, iam_access_key  |  requires: connector credential available  |  rollback: permanent
...
```

`_build_system_prompt()` calls `_build_change_types_text()` instead of using the constant. Everything else in `ai_service.py` is unchanged.

### Change 2: smoke phase `AI_MANIFEST_PLAN`

A new smoke phase in `test_aws_live.py` that:
1. Creates a real project with a hardening goal
2. Sends a single user message asking for a full plan ("Apply a SELinux policy to the nginx service on this host. Propose a full plan now.")
3. Asserts the response contains a `<nexplane-proposal>` block
4. Asserts every `change_type` in the proposals exists in the manifest (no hallucinations)
5. Asserts at least one proposal has a `domain == "hardening"` CR type

This phase does not require a live EC2 instance — it only calls the backend `/projects/{id}/ai/chat` endpoint.

---

## File Map

| File | Status | Change |
|------|--------|--------|
| `backend/app/services/ai_service.py` | Modify | Remove `_CHANGE_TYPES_TEXT`, add `_build_change_types_text()`, update `_build_system_prompt()` |
| `backend/tests/unit/test_ai_service.py` | Modify | Add test for `_build_change_types_text()` — assert manifest CR types appear, grouped by domain |
| `backend/tests/smoke/test_aws_live.py` | Modify | Add `AI_MANIFEST_PLAN` smoke phase |

---

## `_build_change_types_text()` Detail

```python
def _build_change_types_text() -> str:
    from app.services.manifest_builder import get_manifest
    from collections import defaultdict

    entries = get_manifest()
    by_domain: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        by_domain[e["domain"]].append(e)

    lines = []
    for domain in sorted(by_domain):
        lines.append(f"\n## {domain}")
        for e in sorted(by_domain[domain], key=lambda x: x["change_type"]):
            touches = ", ".join(e.get("touches") or [])
            preconditions = "; ".join(e.get("preconditions") or [])
            effects = "; ".join(e.get("effects") or [])
            rollback = e.get("rollback_type", "unknown")
            line = (
                f"{e['change_type']}  —  {e['display_name']}"
                f"  |  touches: {touches}"
                f"  |  requires: {preconditions}"
                f"  |  effects: {effects}"
                f"  |  rollback: {rollback}"
            )
            lines.append(line)
    return "\n".join(lines).strip()
```

Called once per `_build_system_prompt()` invocation. The manifest is cached in memory by `manifest_builder.py` so this is a fast in-process call.

---

## Smoke Phase Detail

The `AI_MANIFEST_PLAN` phase:

1. **Get org settings** — retrieve the configured AI provider + decrypted API key from the platform (already stored in the database)
2. **Create a project** — `POST /projects` with `{"name": "smoke-ai-manifest-...", "goal": "Harden the nginx service with SELinux on this Linux host."}`
3. **Get a server asset** — `GET /assets?asset_type=server` — pick the first result (any registered server asset)
4. **Send one chat message** — `POST /projects/{id}/ai/chat` with `{"message": "Propose a full plan now. Include all required steps.", "asset_ids": [asset_id]}`
5. **Assert proposal present** — response must have `proposed_crs` list, not null
6. **Load manifest** — `GET /cr-manifest` to get the full valid vocabulary
7. **Assert no hallucinations** — every `change_type` in `proposed_crs` must exist in the manifest
8. **Assert hardening present** — at least one proposed CR's `change_type` must be in the `hardening` domain entries from the manifest
9. **Cleanup** — delete the smoke project

---

## Testing

### Unit test addition — `backend/tests/unit/test_ai_service.py`

- `test_build_change_types_text_contains_manifest_types` — call `_build_change_types_text()`, assert `configure_selinux` appears, assert `rotate_iam_key` appears, assert `## hardening` section header present
- `test_build_change_types_text_no_hardcoded_remnants` — assert the old hardcoded groupings like `"EC2 lifecycle:"` are NOT present (confirming the constant was removed)
- `test_build_system_prompt_includes_manifest` — call `_build_system_prompt("harden nginx", [])`, assert the returned string contains `configure_selinux`

### Smoke phase — `AI_MANIFEST_PLAN`

Requirements for pass:
- `proposed_crs` is not null and has at least one entry
- Every `change_type` in `proposed_crs` exists in the full manifest
- At least one `change_type` belongs to the `hardening` domain

---

## System Prompt Impact

Current `## Supported Change Types` section: ~80 CR type names, no metadata.

After integration: 390 CR entries grouped by 13 domains, each with touches, preconditions, effects, and rollback type. The LLM can now:
- Scope to the relevant domain before selecting CR types
- Understand what target asset type each CR acts on (improving `target_assets` accuracy)
- Know upfront whether an action is reversible (improving plan risk communication)
- See preconditions (avoid proposing CRs for which prerequisites aren't met)
