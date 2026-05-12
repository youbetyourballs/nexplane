# Vulnerability Remediation Workflows Design

## Goal
Transform the Vulnerability Remediation tab from a finding tracker into a full remediation lifecycle tool: one-click patch with auto-rollback, AI-guided mitigation when no patch exists, fleet-scale patch campaigns with health gates, and SLA auto-escalation.

## Architecture

**Finding-level actions** on each row in the Findings tab cover single-asset triage. A **Campaigns tab** handles fleet-wide operations as persistent objects. Both surfaces share the same backend CR machinery.

**Tech Stack:** FastAPI (Python), React/TypeScript, existing CR workflow engine, Nexplane agent for seccomp/eBPF/capability controls, AWS security group executor for network isolation.

---

## Section 1: Findings Tab — Expandable Row with Actions

Each finding row gains a ▶ expand toggle. Expanding shows:
- CVE description, CVSS score, affected package + version, fix version
- 6 action buttons: **Patch**, **Mitigate**, **Accept Risk**, **False Positive**, **Assign**, **Blast Radius →**
- Blast Radius inline preview when expanded (count of other affected assets, link to escalate to campaign)

### Action: Patch
One-click. Creates a `patch_packages` CR targeting the affected asset with:
- `packages`: `[{name: affected_package, target_version: fixed_version}]`
- `rollback_strategy`: `nexplane_rollback`
- `verify_after_patch`: `true` (polls `/health` for 60s, auto-rollbacks on failure)

CR status is shown inline in the expanded row. Finding status updates to `remediation_in_progress` → `remediated` on CR completion.

### Action: Mitigate
Opens an AI-recommendations panel inline (see Section 3).

### Action: Accept Risk
Modal: required `reason` text field + `expires_at` date picker (default 90 days). Sets `finding.status = "accepted_risk"`, removes from SLA breach tracking. Shows in a separate "Accepted" filter on the Findings tab.

### Action: False Positive
Modal: required `reason`. Sets `finding.status = "false_positive"`. If the same scanner + CVE + asset combo recurs within 30 days, a flag is shown on the new finding.

### Action: Assign
Dropdown of org users. Sets `finding.assigned_to_user_id`. Assignee sees the finding in a "My Queue" filter. SLA clock continues ticking.

### Action: Blast Radius →
Expands an inline table of all other assets affected by the same CVE (calls existing `/cve/{cve_id}/blast-radius` endpoint). One-click "Create Campaign" button pre-populates the Campaign launcher with all affected assets selected.

---

## Section 2: Campaigns Tab

Persistent campaign list. Campaigns are top-level objects tracking a fleet-wide patch operation.

### Campaign list view
Columns: Name | CVE | Assets | Status | Progress bar | Failed count | Created

Status badges: `Draft` | `Running` | `Paused` | `Complete` | `Failed`

Failed assets shown as a red callout: "N assets failed — rollback applied. Review →"

### Create Campaign
`+ New Campaign` button opens a drawer:
1. **CVE / Package search** — enter CVE ID or package name, shows blast radius results with per-asset checkboxes
2. **Configuration**:
   - Batch size (default: 5 assets/batch)
   - Health gate: wait duration (default: 120s) + health endpoint (default: `/health`)
   - Abort threshold: stop campaign if >20% of a batch fails (configurable)
   - Rollout strategy: `rolling` (default) | `canary` (1 asset first, then rest) | `all_at_once`
3. **Review + Launch** — shows total asset count, estimated duration, and a "Launch Campaign" button

### Campaign detail view
Clicking a campaign shows:
- Batch-by-batch progress (each batch as a row: assets, status, health check result)
- Failed assets list with rollback status and error message
- "Pause" / "Resume" / "Abort" controls
- "Retry Failed" button for assets that failed after rollback

### Backend: Campaign execution
`PatchCampaign` model (new) stores: `id`, `organization_id`, `cve_id`, `target_asset_ids`, `batch_size`, `health_gate_seconds`, `health_endpoint`, `abort_threshold`, `rollout_strategy`, `status`, `batches` (JSON array of batch results).

Execution: `POST /api/v1/vulnerability/campaigns` creates the campaign. A background task processes batches sequentially: create patch CRs for batch → wait for all to complete → run health gate → check abort threshold → next batch.

---

## Section 3: AI-Guided Mitigation

When no patch exists (or operator clicks Mitigate), the finding row expands to show the AI mitigation panel.

### AI recommendation flow
1. Backend calls `_ai_suggest_mitigations(finding)` — sends CVE type, CVSS vector, affected package, asset role (prod/staging), and asset criticality to the AI
2. AI returns a ranked list of controls with confidence scores and plain-English rationale
3. Top 3 are pre-selected (shown with green checkboxes + rationale)
4. Full arsenal shown below as optional add-ons
5. High-impact controls (network isolation, full quarantine) shown in red with ⚠ warning

### Mitigation control types
| Control | Change Type | Reversible |
|---|---|---|
| Network isolation (SG rule) | `security_group_update` | Yes |
| seccomp profile | `agent_ossecurity` (new action) | Yes |
| AppArmor/SELinux policy | `agent_ossecurity` (new action) | Yes |
| WAF rule | `microsegmentation_policy` | Yes |
| Feature flag / config disable | `ssm_command` | Yes |
| Linux capability drop | `agent_ossecurity` (new action) | Yes |
| eBPF runtime block | `agent_ossecurity` (new action) | Yes |
| Rate limiting | `ssm_command` | Yes |
| Virtual patch (IDS rule) | `ssm_command` | Yes |
| Full network isolation | `security_group_update` (deny all) | Yes |

Clicking **Apply N selected mitigations** creates one CR per selected control. Finding status → `mitigated`. Controls link back to the finding for easy reversal.

---

## Section 4: SLA Auto-Escalation

Existing SLA tracking gains automatic escalation actions:

- **T-24h before breach**: notify assignee via in-app notification (new `Notification` model)
- **At breach**: finding flagged as `breached` (already exists), plus:
  - If unassigned: auto-assign to org admin
  - If `approval_level = "auto_execute"` on the matched policy: auto-approve and execute the patch CR without human intervention
- **48h past breach**: severity is programmatically upgraded (e.g., `high` → `critical`) and re-queued at top of findings list

New `PUT /api/v1/vulnerability/sla/config` gains additional fields:
- `escalate_to_user_id` — user to auto-assign on breach (optional)
- `auto_execute_on_breach` — boolean, default false. When true, auto-approves (not bypasses) the patch CR at breach — safety engine and preflight checks still run
- `severity_upgrade_hours` — hours past breach before upgrading severity (default 48)

---

## Data Model Changes

### VulnerabilityFinding (extend existing)
- `assigned_to_user_id: uuid | null`
- `accepted_risk_reason: str | null`
- `accepted_risk_expires_at: datetime | null`
- `mitigations: JSON[]` — list of applied mitigation CR IDs + control type

### PatchCampaign (new table)
- `id`, `organization_id`, `title`, `cve_id`
- `target_asset_ids: JSON`
- `batch_size: int`, `health_gate_seconds: int`, `health_endpoint: str`
- `abort_threshold: float`, `rollout_strategy: str`
- `status: enum(draft, running, paused, complete, failed, aborted)`
- `batches: JSON` — array of `{asset_ids, status, cr_ids, health_check_result, error}`
- `created_at`, `started_at`, `completed_at`

### SLAConfig (extend existing JSON blob)
- `escalate_to_user_id`, `auto_execute_on_breach`, `severity_upgrade_hours`

---

## API Changes

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/vulnerability/findings/{id}/patch` | One-click patch action |
| POST | `/api/v1/vulnerability/findings/{id}/mitigate` | Apply selected mitigations |
| PATCH | `/api/v1/vulnerability/findings/{id}/assign` | Assign to user |
| POST | `/api/v1/vulnerability/campaigns` | Create patch campaign |
| GET | `/api/v1/vulnerability/campaigns` | List campaigns |
| GET | `/api/v1/vulnerability/campaigns/{id}` | Campaign detail |
| POST | `/api/v1/vulnerability/campaigns/{id}/pause` | Pause campaign |
| POST | `/api/v1/vulnerability/campaigns/{id}/resume` | Resume campaign |
| POST | `/api/v1/vulnerability/campaigns/{id}/abort` | Abort campaign |
| POST | `/api/v1/vulnerability/campaigns/{id}/retry-failed` | Retry failed assets |
| GET | `/api/v1/vulnerability/findings/{id}/mitigations` | AI mitigation suggestions |

---

## Frontend Components

**Modified:**
- `VulnerabilityRemediation.tsx` — add Campaigns tab, wire finding row expand/actions
- `FindingQueue.tsx` — add expandable rows, action buttons, inline status

**New:**
- `FindingActionPanel.tsx` — the expanded inline panel with all 6 actions
- `MitigationPanel.tsx` — AI-recommended controls with pre-selection
- `PatchCampaignList.tsx` — campaign list with progress bars and failed callouts
- `CreateCampaignDrawer.tsx` — CVE search → asset selection → config → launch
- `CampaignDetail.tsx` — batch-by-batch view with retry controls

---

## Smoke Test Coverage
- `VULN_A`: one-click patch on a finding, verify CR created and finding status updated
- `VULN_B`: mitigate with network isolation, verify security group updated
- `VULN_C`: campaign with 2 assets, verify batching and health gate
- `VULN_D`: accept risk with expiry, verify finding removed from SLA dashboard
- `VULN_E`: SLA breach auto-escalation, verify auto-assign fires
