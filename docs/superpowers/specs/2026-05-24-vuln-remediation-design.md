# Vulnerability Remediation Workflows — Full Lifecycle Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close the loop between a vulnerability finding and confirmed remediation — from ingest through PoC validation, human-driven action selection (patch + defense-in-depth mitigations), CR execution, automated verification, and regression detection — all from within the finding itself.

**Architecture:** Finding-as-control-center. Every CR created from a finding is tracked back to it via a new `FindingChangeRequest` join table. The finding drives its own lifecycle through well-defined states, with PoC validation as the exploitability adjudicator and automated scanner re-probe as the verification gate. No self-reporting.

**Tech Stack:** FastAPI (backend), SQLAlchemy async (DB), React + TanStack Query (frontend), APScheduler (verification + regression jobs), Nessus/OpenVAS scanner connectors (PoC + verification), CISA KEV catalog (daily cached refresh), Metasploit/ExploitDB (PoC discovery)

---

## Finding Lifecycle States

```
open → exploitability_pending → actionable → remediating → verifying → resolved
            ↓                       ↓
        challenged            risk_accepted | false_positive
            ↓
    (PoC result adjudicates)
                                                    ↓ (regression detected)
                                                 regressed
```

| State | Meaning | SLA clock |
|---|---|---|
| `open` | Ingested, no action taken | Running |
| `exploitability_pending` | PoC found, running against asset | Running |
| `challenged` | Engineer claims not exploitable; PoC is adjudicating | Running |
| `actionable` | Exploitability confirmed or inconclusive; ready for action | Running |
| `remediating` | One or more CRs in flight (patch and/or mitigations) | Running |
| `verifying` | All CRs executed; scanner re-probe firing | Running |
| `resolved` | Scanner confirms remediation | Stopped |
| `regressed` | Previously resolved; scanner re-detected outside intentional rollback | Restarts at emergency tier |
| `risk_accepted` | Engineer accepted risk with expiry date | Paused until expiry |
| `false_positive` | Closed without SLA credit | Stopped |

**CISA KEV override:** If CVE is on the CISA Known Exploited Vulnerabilities catalog, finding is immediately marked `exploited` and escalated to emergency SLA tier. The exploitability challenge flow is disabled. No debate.

---

## PoC Validation

### Discovery (priority order)

1. **CISA KEV catalog** — checked first; if present, skip PoC execution entirely; mark `exploited`
2. **Metasploit module index** — query `modules/exploits` for CVE ID; highest confidence
3. **ExploitDB** — search published PoCs by CVE ID
4. **NVD `exploitabilityScore`** — fallback signal only; does not trigger automated execution

### Execution

PoC runs are dispatched as a new CR type: `vuln_poc_validate`.

- Runner: ephemeral EC2 instance in an isolated subnet (never against prod directly); same AMI cache pattern as smoke tests
- For credentialed checks: use the existing Nessus/OpenVAS connector to run a targeted plugin check against the asset
- For Metasploit modules: sandboxed runner EC2 executes the module with `check` mode first, then `exploit` only if `check` confirms vulnerable

### Results and state transitions

| Result | State transition | SLA effect |
|---|---|---|
| `exploited` | `open/challenged` → `actionable` (challenge overruled) | Escalate to emergency tier |
| `not_exploited` | `challenged` → `actionable` (challenge validated) | Downgrade one SLA tier |
| `inconclusive` | Stay in current state | No change |
| `no_poc_available` | Stay in current state | No change; human decides |

---

## Remediation Action Panel (UI)

The finding card expands into a full action panel. All lifecycle actions live here.

### Exploitability bar

- CVE ID, CVSS score, severity badge
- PoC status badge: `KEV confirmed` | `PoC — exploited` | `PoC — not exploited` | `No PoC available`
- **Challenge exploitability** button: opens text field for engineer's reasoning; triggers PoC run if not already done; disabled if CISA KEV
- SLA countdown: time remaining at current tier with tier label (30-day warning / 14-day escalation / 7-day emergency / emergency)

### Affected assets

- All assets affected by this CVE (blast radius query)
- Per asset: hostname, OS, installed version, fixed version if known
- Checkbox to select which assets to remediate; default all

### Remediation tabs

**Patch tab**
- Shown when a fixed version exists; grayed out with "No patch available — use mitigations" when none
- Current → fixed version diff per selected asset
- Configurable batch size and rolling strategy
- "Create patch CR" — one CR per asset batch, linked to this finding

**Mitigate tab**
- Full ranked list of applicable controls from `vuln_mitigation_service`:
  - Network isolation, WAF rule, seccomp profile, AppArmor/SELinux tightening, feature flag / config disable, rate limiting, virtual patch (IDS/IPS rule), eBPF runtime block, Linux capability drop, process namespace isolation, full quarantine
- Each control shows: name, description, estimated service impact (low / medium / high)
- **Multi-select — any combination.** Defense in depth is the default posture.
- "Apply selected mitigations" — one CR per selected control, all linked to this finding
- Both tabs usable simultaneously: patch + stack mitigations while patch deploys

### Active remediation tracker

- Live list of all CRs from this finding: type (patch / mitigation / poc_validate), status, progress
- Each row links to full CR detail; inline rollback button
- When all CRs reach `executed`: verification probe fires automatically

### Verification result

- Scanner re-probe result: `confirmed resolved` | `still vulnerable` | `partial — N of M assets resolved`
- If still vulnerable: suggests next actions (add mitigations, escalate, accept risk)
- Timestamp of last probe; "Re-verify now" button

### Accept risk / false positive

- Bottom of panel — escape hatches, not primary actions
- Accept risk requires expiry date; appears on SLA dashboard as accepted with countdown to re-review
- False positive closes finding without SLA credit

---

## Backend Architecture

### New: `FindingChangeRequest` join table

```python
class FindingChangeRequest(Base):
    finding_id: UUID  # FK → vulnerability_findings
    cr_id: UUID       # FK → change_requests
    role: str         # "patch" | "mitigation" | "poc_validate" | "verify"
    created_at: datetime
```

Replaces the existing `mitigations` JSON field for CR tracking. Migration: existing `mitigations` JSON entries are backfilled into `FindingChangeRequest` rows on first deploy; the `mitigations` column is retained but deprecated (no longer written to). Enables finding panel to query all associated CRs in one call and display live status without polling the Change Requests endpoint separately.

### New service: `app/services/vuln_poc_service.py`

Three responsibilities:

- `check_cisa_kev(cve_id: str) -> bool` — queries cached CISA KEV JSON (refreshed daily via APScheduler); if present, immediately escalates finding
- `discover_poc(cve_id: str) -> PocDiscoveryResult` — checks Metasploit module index and ExploitDB; returns `{source, confidence, poc_ref}`
- `dispatch_poc_run(finding, asset, db) -> ChangeRequest` — creates `vuln_poc_validate` CR; executor runs credentialed Nessus/OpenVAS plugin check or sandboxed Metasploit runner; result updates `finding.exploitability_result` and triggers state transition

### New service: `app/services/vuln_verification_service.py`

Triggered by APScheduler after all remediation CRs for a finding reach `executed`:

1. Identifies which scanner originally reported the finding
2. Issues targeted re-scan of affected asset via scanner connector (Nessus `POST /scans/{id}/launch` with asset scope; or OpenVAS task re-run)
3. Polls for scan completion (30 min timeout)
4. If finding absent from results: `finding.status = "resolved"`, record timestamp, stop SLA clock
5. If finding present: `finding.status = "open"`, set `verification_failed` flag, surface next-action suggestions

### Regression watcher (extension of `_ingest_findings_background`)

On every scanner ingest, for each incoming finding:

1. Check if a `resolved` finding exists with matching `cve_id + asset_id`
2. If yes: check most recent Nexplane CR on that asset
   - **Nexplane rollback:** link re-opened finding to rollback CR; status = `regressed (intentional rollback)`; no SLA restart; no notification escalation
   - **Not a rollback:** status = `regressed`; SLA clock restarts at emergency tier; assigned owner notified immediately

### SLA escalation triggers (extensions to existing config)

| Trigger | Effect |
|---|---|
| CISA KEV match on ingest | Immediate emergency tier; admin notification |
| PoC result = `exploited` | Immediate emergency tier |
| PoC result = `not_exploited` (challenge validated) | Downgrade one SLA tier |
| Finding `regressed` (non-rollback) | Restart SLA at emergency tier |
| All CRs executed, verification `still_vulnerable` | Notify assigned owner; suggest next actions |

### New CR type: `vuln_poc_validate`

- Executor: `app/connectors/executors/vuln/poc_validate.py`
- Parameters: `cve_id`, `asset_id`, `poc_source` (metasploit_module | nessus_plugin | exploitdb_ref), `poc_ref`
- Rollback action: null (read-only probe; isolation is handled by the ephemeral runner subnet)
- Result schema: `{result: exploited | not_exploited | inconclusive, evidence: str, scanner_output: str}`

---

## New API Endpoints

```
POST   /vulnerability/findings/{id}/poc-validate          — trigger PoC run
GET    /vulnerability/findings/{id}/poc-result            — get current PoC result
GET    /vulnerability/findings/{id}/change-requests       — all CRs linked to this finding
POST   /vulnerability/findings/{id}/verify                — manually trigger verification re-probe
GET    /vulnerability/findings/{id}/verification-result   — latest verification result
POST   /vulnerability/findings/{id}/challenge             — submit exploitability challenge
```

Existing endpoints unchanged. `PATCH /findings/{id}/status` gains `regressed` and `exploitability_pending` as valid status values.

---

## Smoke Testing

### New phase: `VULN_REMEDIATION`

Full end-to-end lifecycle against real infrastructure. Uses the NESSUS_SCAN AMI cache for the scanner.

1. Ingest synthetic CVE finding via webhook (CVE-2021-44228, Nessus plugin 155999)
2. Assert finding appears in `open` state with SLA clock running
3. Trigger PoC validation — credentialed Nessus check against smoke asset
4. Assert PoC result is `exploited` or `not_exploited` (not `inconclusive`)
5. Assert finding state transitions correctly based on result
6. Create patch CR from finding; execute it; assert `executed`
7. Assert verification probe fires automatically within 5 minutes
8. Assert finding transitions to `resolved` (or `verification_failed` with correct flags)
9. Simulate regression: re-ingest same CVE finding; assert `regressed` with emergency SLA tier
10. Rollback patch CR; re-ingest; assert finding links to rollback CR and gets `regressed (intentional rollback)`

### Unit tests

- `test_poc_service.py` — CISA KEV lookup, PoC discovery logic, state transition rules per result
- `test_vuln_remediation_engine.py` — extend to cover `FindingChangeRequest` join, SLA tier escalation on PoC result
- `test_regression_watcher.py` — mock ingest with resolved finding; assert correct behavior for rollback vs non-rollback regression

---

## Files Created or Modified

| File | Change |
|---|---|
| `backend/app/models/vulnerability.py` | Add `FindingChangeRequest` model; add `exploitability_result`, `poc_source`, `poc_ref` fields to `VulnerabilityFinding`; add `regressed`, `exploitability_pending`, `challenged` to status enum |
| `backend/app/services/vuln_poc_service.py` | **New** — CISA KEV check, PoC discovery, PoC run dispatch |
| `backend/app/services/vuln_verification_service.py` | **New** — post-execution scanner re-probe, result handling |
| `backend/app/services/vuln_remediation_engine.py` | Extend to write `FindingChangeRequest` records on CR creation; add SLA escalation triggers |
| `backend/app/routers/vulnerability.py` | Add 6 new endpoints (poc-validate, poc-result, challenge, change-requests, verify, verification-result) |
| `backend/app/connectors/executors/vuln/poc_validate.py` | **New** — `vuln_poc_validate` CR executor |
| `backend/app/connectors/change_type_definitions/vuln_poc_validate.json` | **New** — CTD for poc_validate CR type |
| `backend/app/services/scheduler_service.py` | Wire CISA KEV daily refresh; wire verification trigger on CR completion |
| `backend/tests/smoke/test_aws_live.py` | Add `VULN_REMEDIATION` phase |
| `backend/tests/unit/test_poc_service.py` | **New** |
| `backend/tests/unit/test_regression_watcher.py` | **New** |
| `frontend/src/components/FindingActionPanel.tsx` | Full rewrite — exploitability bar, PoC status, two-tab remediation, CR tracker, verification result |
| `frontend/src/components/MitigationPanel.tsx` | Extend — multi-select, defense-in-depth mode, service impact labels |
| `frontend/src/pages/VulnerabilityRemediation.tsx` | Wire new finding state badges, SLA escalation indicators |
