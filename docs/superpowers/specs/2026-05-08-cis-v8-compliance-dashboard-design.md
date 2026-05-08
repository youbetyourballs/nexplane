# CIS Controls v8 Compliance Dashboard — Design Spec

## Goal

Replace the current per-asset compliance page with an 18-control CIS Controls v8 dashboard. The primary view is a table of all 18 controls with per-control compliance scores, drillable to individual benchmark checks and then to the assets failing each check.

## Architecture

**Approach:** Compute-on-the-fly (Option A). No new DB models. A new `GET /compliance/cis-summary` endpoint scans all org assets, reads `asset_metadata.cis_compliance.latest.controls` from each, groups results by a static control map, and returns the 18-control summary. Fast enough at current scale; see Future Scale section.

---

## Data Model

No new database tables. All CIS audit data is already stored per-asset in `asset_metadata.cis_compliance.latest.controls` as a `ControlResult[]` array, written by the `write_cis_score_to_metadata()` function after each audit job.

### CIS v8 Control Map — `backend/app/compliance/cis_v8_map.py`

A static Python module defining all 18 controls, their scoring method, and which CIS Benchmark section prefixes feed them:

```python
CIS_V8_CONTROLS = [
    {
        "id": 1,
        "name": "Inventory and Control of Enterprise Assets",
        "method": "asset_coverage",
        "benchmark_sections": [],          # scored from Nexplane inventory, not agent
    },
    {
        "id": 2,
        "name": "Inventory and Control of Software Assets",
        "method": "not_tracked",
        "benchmark_sections": [],
    },
    {
        "id": 3,
        "name": "Data Protection",
        "method": "not_tracked",
        "benchmark_sections": [],
    },
    {
        "id": 4,
        "name": "Secure Configuration of Enterprise Assets and Software",
        "method": "agent_audit",
        "benchmark_sections": ["1.", "3."],  # filesystem config, network config
    },
    {
        "id": 5,
        "name": "Account Management",
        "method": "agent_audit",
        "benchmark_sections": ["5.1", "5.2"],  # users, groups, SSH
    },
    {
        "id": 6,
        "name": "Access Control Management",
        "method": "agent_audit",
        "benchmark_sections": ["5.3", "5.4"],  # sudo, PAM, file permissions
    },
    {
        "id": 7,
        "name": "Continuous Vulnerability Management",
        "method": "agent_audit",
        "benchmark_sections": ["2."],           # services / installed packages
    },
    {
        "id": 8,
        "name": "Audit Log Management",
        "method": "agent_audit",
        "benchmark_sections": ["4."],           # auditd, rsyslog
    },
    # Controls 9–18: not_tracked, no benchmark_sections
    {"id": 9,  "name": "Email and Web Browser Protections",  "method": "not_tracked", "benchmark_sections": []},
    {"id": 10, "name": "Malware Defenses",                   "method": "not_tracked", "benchmark_sections": []},
    {"id": 11, "name": "Data Recovery",                      "method": "not_tracked", "benchmark_sections": []},
    {"id": 12, "name": "Network Infrastructure Management",  "method": "not_tracked", "benchmark_sections": []},
    {"id": 13, "name": "Network Monitoring and Defense",     "method": "not_tracked", "benchmark_sections": []},
    {"id": 14, "name": "Security Awareness and Skills Training", "method": "not_tracked", "benchmark_sections": []},
    {"id": 15, "name": "Service Provider Management",        "method": "not_tracked", "benchmark_sections": []},
    {"id": 16, "name": "Application Software Security",      "method": "not_tracked", "benchmark_sections": []},
    {"id": 17, "name": "Incident Response Management",       "method": "not_tracked", "benchmark_sections": []},
    {"id": 18, "name": "Penetration Testing",                "method": "not_tracked", "benchmark_sections": []},
]
```

### API Response Shape — `GET /compliance/cis-summary`

```json
{
  "overall_score": 0.68,
  "tracked_controls": 6,
  "last_updated": "2026-05-08T12:00:00Z",
  "controls": [
    {
      "id": 1,
      "name": "Inventory and Control of Enterprise Assets",
      "method": "asset_coverage",
      "score": 0.94,
      "assets_passing": 11,
      "assets_total": 12,
      "checks": [
        {
          "id": "1.1",
          "title": "All enterprise assets tracked with active connector",
          "pass_count": 11,
          "fail_count": 1,
          "failing_assets": [
            { "id": "uuid", "name": "prod-db-01", "detail": "No connector linked" }
          ]
        }
      ]
    },
    {
      "id": 4,
      "name": "Secure Configuration of Enterprise Assets and Software",
      "method": "agent_audit",
      "score": 0.72,
      "assets_passing": 8,
      "assets_total": 12,
      "checks": [
        {
          "id": "4.1",
          "title": "SSH PermitRootLogin disabled",
          "pass_count": 11,
          "fail_count": 1,
          "failing_assets": [
            { "id": "uuid", "name": "prod-db-01", "detail": "Expected: no  Got: yes" }
          ]
        }
      ]
    },
    {
      "id": 2,
      "name": "Inventory and Control of Software Assets",
      "method": "not_tracked",
      "score": null,
      "assets_passing": null,
      "assets_total": null,
      "checks": []
    }
  ]
}
```

**Scoring logic:**

- `asset_coverage` (Control 1): `score = assets_with_connector_id / total_assets`. An asset "passes" if it has a non-null `connector_id`.
- `agent_audit`: For each asset with CIS audit data, filter `ControlResult[]` to entries whose `section` starts with any of the control's `benchmark_sections`. An asset "passes" a control if all of its matching checks pass (100% threshold — a single failing check means the asset fails that control). `score = passing_assets / audited_assets`. Assets with no audit data are excluded from the denominator (not penalised for being unaudited).
- `not_tracked`: `score = null`.
- `overall_score`: mean of non-null control scores, or null if no tracked controls have data.
- `last_updated`: most recent `collected_at` timestamp across all assets' CIS data, or null.

**Check aggregation for `agent_audit` controls:**

Each unique `(section, title)` pair across all assets becomes one check row. `pass_count` and `fail_count` are the number of assets where that check passed or failed respectively. `failing_assets` lists each asset that failed, with `detail = "Expected: {expected_value}  Got: {current_value}"`.

---

## Backend Changes

### New file: `backend/app/compliance/cis_v8_map.py`
Static control definitions as shown above.

### Modified: `backend/app/routers/compliance.py`
Add `GET /compliance/cis-summary` endpoint:
1. Query all `Asset` records for the org where `asset_type == server`
2. For each control in `CIS_V8_CONTROLS`:
   - If `not_tracked`: append stub entry with `score=null`
   - If `asset_coverage`: count assets with/without `connector_id`
   - If `agent_audit`: scan `asset_metadata.cis_compliance.latest.controls`, filter by `benchmark_sections`, aggregate per unique check title
3. Return full 18-control array with `overall_score` and `last_updated`

No authentication change — same org-scoped auth as other compliance endpoints.

---

## Frontend Changes

### Replaced: `frontend/src/pages/Compliance.tsx`

Full replacement. Remove:
- Per-asset audit score table
- Drift alerts section
- Unaudited assets section

New page structure:
```
Header: "Compliance" + [Run Full Audit] button
Sub-header: "CIS Controls v8 · Last updated X min ago" + overall score bar

Table (18 rows, one per control):
  Columns: #, Control Name, Score (progress bar), Assets (X/Y), Status badge
  Tracked controls: clickable, expand on click
  Not-tracked controls: greyed out, "Not tracked" badge, not expandable

Expanded control row:
  Checks sub-table: Check ID, Title, Pass, Fail, Total
  Each check row is expandable

Expanded check row:
  Failing assets list: asset name, detail string (expected vs got)
```

**Status badge logic:**
- `score >= 0.80`: ✅ green
- `score >= 0.60`: ⚠️ amber
- `score < 0.60`: 🔴 red
- `score == null`: · Not tracked (muted grey)

**Run Full Audit button:**
Iterates all `server` assets in inventory and fires `agent_compliance` change requests in parallel (same CR flow as existing per-asset "Run Audit" button, just batched). Shows a spinner while any audit is in flight. Refetches `/compliance/cis-summary` on completion.

### Modified: `frontend/src/api/endpoints.ts`
Add `complianceApi.getSummary()` → `GET /compliance/cis-summary`.

---

## What Is Removed

The following features on the current Compliance page are removed in this redesign:

- Per-asset score badges and trend sparklines
- The "Audited Assets" / "Unaudited Assets" split
- Drift alerts display (DRAFT `enforce_cis_benchmark` CRs)
- Per-asset "Re-audit" buttons (replaced by top-level "Run Full Audit")
- Evidence collection button (this functionality moves to Settings or a future Evidence tab)
- "New CIS Campaign" button

The `ComplianceBaseline` CRUD and freeze window endpoints remain in the backend unchanged; their UI surface is deferred to a future settings panel.

---

## Files Affected

| File | Change |
|------|--------|
| `backend/app/compliance/cis_v8_map.py` | **New** — static CIS v8 control definitions |
| `backend/app/routers/compliance.py` | Add `GET /compliance/cis-summary` endpoint |
| `frontend/src/pages/Compliance.tsx` | **Full replacement** |
| `frontend/src/api/endpoints.ts` | Add `complianceApi.getSummary()` |

---

## Future Scale Note

When asset count exceeds ~500, the on-the-fly scan across all assets will become slow. At that point, introduce a `ControlFamilyScore` table (one row per control per org) materialized by a post-audit background job. The API response shape is identical — only the data source changes. This is explicitly out of scope for this implementation.

## Related Future Work

See backlog: **CIS Controls v8 — expand coverage beyond current agent capabilities**. This design covers 6 of 18 controls. Future work will expand coverage through new agent commands (software inventory for Control 2), scanner ingest (Tenable/Qualys/CrowdStrike for Control 7/10), and manual attestation for Controls 14/15. Requires a separate design session.
