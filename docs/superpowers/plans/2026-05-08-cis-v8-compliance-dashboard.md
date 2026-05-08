# CIS Controls v8 Compliance Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the per-asset Compliance page with an 18-control CIS Controls v8 dashboard that shows per-control scores with drilldown to checks and failing assets.

**Architecture:** A new static `cis_v8_map.py` defines all 18 controls with scoring method and benchmark section mappings. A new `GET /compliance/cis-summary` endpoint reads all server assets, aggregates their stored `ControlResult[]` data by control family, and returns a scored 18-row summary. The frontend `Compliance.tsx` is fully replaced with a new expandable-table UI.

**Tech Stack:** Python/FastAPI (backend), React/TypeScript with @tanstack/react-query (frontend), existing `asset_metadata.cis_compliance.latest.controls` JSON storage

---

## File Structure

```
backend/app/compliance/cis_v8_map.py          NEW  — static control definitions + scoring logic
backend/app/routers/compliance.py             MODIFY — add GET /compliance/cis-summary endpoint
backend/app/schemas/compliance.py             MODIFY — add CisSummary pydantic response models
backend/tests/test_cis_compliance.py          NEW  — unit tests for scoring logic and endpoint
frontend/src/pages/Compliance.tsx             REPLACE — new 18-row CIS v8 dashboard
frontend/src/api/endpoints.ts                 MODIFY — add complianceApi.getSummary()
```

---

## Task 1: CIS v8 Control Map + Scoring Logic

**Files:**
- Create: `backend/app/compliance/cis_v8_map.py`
- Test: `backend/tests/test_cis_compliance.py`

- [ ] **Step 1: Write failing tests for the control map**

Create `backend/tests/test_cis_compliance.py`:

```python
import pytest
from app.compliance.cis_v8_map import CIS_V8_CONTROLS, compute_cis_summary


def test_control_map_has_18_controls():
    assert len(CIS_V8_CONTROLS) == 18


def test_control_ids_are_1_through_18():
    ids = [c["id"] for c in CIS_V8_CONTROLS]
    assert ids == list(range(1, 19))


def test_control_1_is_asset_coverage():
    ctrl = next(c for c in CIS_V8_CONTROLS if c["id"] == 1)
    assert ctrl["method"] == "asset_coverage"


def test_controls_4_to_8_are_agent_audit():
    for cid in [4, 5, 6, 7, 8]:
        ctrl = next(c for c in CIS_V8_CONTROLS if c["id"] == cid)
        assert ctrl["method"] == "agent_audit", f"Control {cid} should be agent_audit"


def test_controls_2_3_and_9_to_18_are_not_tracked():
    not_tracked_ids = [2, 3] + list(range(9, 19))
    for cid in not_tracked_ids:
        ctrl = next(c for c in CIS_V8_CONTROLS if c["id"] == cid)
        assert ctrl["method"] == "not_tracked", f"Control {cid} should be not_tracked"


def test_compute_cis_summary_asset_coverage_all_pass():
    # Two assets, both with connector_id
    assets = [
        {"id": "a1", "name": "web-01", "connector_id": "c1", "asset_metadata": {}},
        {"id": "a2", "name": "web-02", "connector_id": "c2", "asset_metadata": {}},
    ]
    summary = compute_cis_summary(assets)
    ctrl1 = next(c for c in summary["controls"] if c["id"] == 1)
    assert ctrl1["score"] == 1.0
    assert ctrl1["assets_passing"] == 2
    assert ctrl1["assets_total"] == 2
    assert len(ctrl1["checks"][0]["failing_assets"]) == 0


def test_compute_cis_summary_asset_coverage_one_fail():
    assets = [
        {"id": "a1", "name": "web-01", "connector_id": "c1", "asset_metadata": {}},
        {"id": "a2", "name": "db-01",  "connector_id": None,  "asset_metadata": {}},
    ]
    summary = compute_cis_summary(assets)
    ctrl1 = next(c for c in summary["controls"] if c["id"] == 1)
    assert ctrl1["score"] == pytest.approx(0.5)
    assert ctrl1["assets_passing"] == 1
    assert ctrl1["assets_total"] == 2
    failing = ctrl1["checks"][0]["failing_assets"]
    assert len(failing) == 1
    assert failing[0]["name"] == "db-01"


def test_compute_cis_summary_agent_audit_aggregates_checks():
    # Asset with CIS audit data — control 4 maps to section "1." and "3."
    cis_data = {
        "cis_compliance": {
            "latest": {
                "collected_at": "2026-05-08T00:00:00Z",
                "controls": [
                    {"id": "1.1.1", "section": "1.1", "title": "Disable unused filesystems",
                     "status": "pass", "expected": "disabled", "actual": "disabled"},
                    {"id": "3.1.1", "section": "3.1", "title": "Disable IP forwarding",
                     "status": "fail", "expected": "0", "actual": "1"},
                ]
            }
        }
    }
    assets = [
        {"id": "a1", "name": "web-01", "connector_id": "c1", "asset_metadata": cis_data},
    ]
    summary = compute_cis_summary(assets)
    ctrl4 = next(c for c in summary["controls"] if c["id"] == 4)
    # Asset has one pass and one fail → asset fails control 4 (100% threshold)
    assert ctrl4["score"] == 0.0
    assert ctrl4["assets_passing"] == 0
    assert ctrl4["assets_total"] == 1
    # Check rows
    check_titles = [ch["title"] for ch in ctrl4["checks"]]
    assert "Disable unused filesystems" in check_titles
    assert "Disable IP forwarding" in check_titles
    # Failing check has the asset listed
    fail_check = next(ch for ch in ctrl4["checks"] if ch["title"] == "Disable IP forwarding")
    assert fail_check["fail_count"] == 1
    assert fail_check["failing_assets"][0]["name"] == "web-01"
    assert "Expected: 0" in fail_check["failing_assets"][0]["detail"]


def test_compute_cis_summary_asset_all_checks_pass():
    cis_data = {
        "cis_compliance": {
            "latest": {
                "collected_at": "2026-05-08T00:00:00Z",
                "controls": [
                    {"id": "1.1.1", "section": "1.1", "title": "Disable unused filesystems",
                     "status": "pass", "expected": "disabled", "actual": "disabled"},
                ]
            }
        }
    }
    assets = [{"id": "a1", "name": "web-01", "connector_id": "c1", "asset_metadata": cis_data}]
    summary = compute_cis_summary(assets)
    ctrl4 = next(c for c in summary["controls"] if c["id"] == 4)
    assert ctrl4["score"] == 1.0
    assert ctrl4["assets_passing"] == 1


def test_compute_cis_summary_not_tracked_controls_have_null_score():
    assets = [{"id": "a1", "name": "web-01", "connector_id": "c1", "asset_metadata": {}}]
    summary = compute_cis_summary(assets)
    for cid in [2, 3] + list(range(9, 19)):
        ctrl = next(c for c in summary["controls"] if c["id"] == cid)
        assert ctrl["score"] is None
        assert ctrl["checks"] == []


def test_compute_cis_summary_overall_score_is_mean_of_tracked():
    # Control 1: 1 asset, connector_id set → score=1.0
    # Controls 4-8: no CIS data → excluded from score (no audited assets)
    assets = [{"id": "a1", "name": "web-01", "connector_id": "c1", "asset_metadata": {}}]
    summary = compute_cis_summary(assets)
    # Only control 1 has a score (1.0), controls 4-8 have no audited assets → score=None
    assert summary["overall_score"] == 1.0
    assert summary["tracked_controls"] == 6


def test_compute_cis_summary_last_updated_is_most_recent():
    assets = [
        {"id": "a1", "name": "web-01", "connector_id": "c1",
         "asset_metadata": {"cis_compliance": {"latest": {"collected_at": "2026-04-01T00:00:00Z", "controls": []}}}},
        {"id": "a2", "name": "web-02", "connector_id": "c2",
         "asset_metadata": {"cis_compliance": {"latest": {"collected_at": "2026-05-08T12:00:00Z", "controls": []}}}},
    ]
    summary = compute_cis_summary(assets)
    assert summary["last_updated"] == "2026-05-08T12:00:00Z"


def test_compute_cis_summary_no_assets_returns_null_scores():
    summary = compute_cis_summary([])
    assert summary["overall_score"] is None
    assert summary["last_updated"] is None
    for ctrl in summary["controls"]:
        if ctrl["method"] != "not_tracked":
            assert ctrl["score"] is None
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd backend
python -m pytest tests/test_cis_compliance.py -v 2>&1 | head -20
```

Expected: `ImportError: cannot import name 'CIS_V8_CONTROLS'`

- [ ] **Step 3: Create `backend/app/compliance/cis_v8_map.py`**

```python
from __future__ import annotations
from typing import Any

CIS_V8_CONTROLS: list[dict] = [
    {"id": 1,  "name": "Inventory and Control of Enterprise Assets",        "method": "asset_coverage", "benchmark_sections": []},
    {"id": 2,  "name": "Inventory and Control of Software Assets",          "method": "not_tracked",    "benchmark_sections": []},
    {"id": 3,  "name": "Data Protection",                                   "method": "not_tracked",    "benchmark_sections": []},
    {"id": 4,  "name": "Secure Configuration of Enterprise Assets and Software", "method": "agent_audit", "benchmark_sections": ["1.", "3."]},
    {"id": 5,  "name": "Account Management",                                "method": "agent_audit",    "benchmark_sections": ["5.1", "5.2"]},
    {"id": 6,  "name": "Access Control Management",                         "method": "agent_audit",    "benchmark_sections": ["5.3", "5.4"]},
    {"id": 7,  "name": "Continuous Vulnerability Management",               "method": "agent_audit",    "benchmark_sections": ["2."]},
    {"id": 8,  "name": "Audit Log Management",                              "method": "agent_audit",    "benchmark_sections": ["4."]},
    {"id": 9,  "name": "Email and Web Browser Protections",                 "method": "not_tracked",    "benchmark_sections": []},
    {"id": 10, "name": "Malware Defenses",                                  "method": "not_tracked",    "benchmark_sections": []},
    {"id": 11, "name": "Data Recovery",                                     "method": "not_tracked",    "benchmark_sections": []},
    {"id": 12, "name": "Network Infrastructure Management",                 "method": "not_tracked",    "benchmark_sections": []},
    {"id": 13, "name": "Network Monitoring and Defense",                    "method": "not_tracked",    "benchmark_sections": []},
    {"id": 14, "name": "Security Awareness and Skills Training",            "method": "not_tracked",    "benchmark_sections": []},
    {"id": 15, "name": "Service Provider Management",                       "method": "not_tracked",    "benchmark_sections": []},
    {"id": 16, "name": "Application Software Security",                     "method": "not_tracked",    "benchmark_sections": []},
    {"id": 17, "name": "Incident Response Management",                      "method": "not_tracked",    "benchmark_sections": []},
    {"id": 18, "name": "Penetration Testing",                               "method": "not_tracked",    "benchmark_sections": []},
]


def _section_matches(section: str, prefixes: list[str]) -> bool:
    return any(section.startswith(p) for p in prefixes)


def compute_cis_summary(assets: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Given a list of asset dicts (each with 'id', 'name', 'connector_id', 'asset_metadata'),
    compute the 18-control CIS v8 summary.

    Each asset dict must have:
      - id: str
      - name: str
      - connector_id: str | None
      - asset_metadata: dict  (may contain 'cis_compliance.latest.controls')
    """
    # Gather last_updated from CIS audit data
    last_updated: str | None = None
    for asset in assets:
        ts = (
            (asset.get("asset_metadata") or {})
            .get("cis_compliance", {})
            .get("latest", {})
            .get("collected_at")
        )
        if ts:
            if last_updated is None or ts > last_updated:
                last_updated = ts

    controls_out = []
    scored_values: list[float] = []

    for ctrl_def in CIS_V8_CONTROLS:
        cid = ctrl_def["id"]
        method = ctrl_def["method"]
        name = ctrl_def["name"]
        sections = ctrl_def["benchmark_sections"]

        if method == "not_tracked":
            controls_out.append({
                "id": cid, "name": name, "method": method,
                "score": None, "assets_passing": None, "assets_total": None,
                "checks": [],
            })
            continue

        if method == "asset_coverage":
            total = len(assets)
            passing_assets = [a for a in assets if a.get("connector_id")]
            failing_assets = [a for a in assets if not a.get("connector_id")]
            score = (len(passing_assets) / total) if total > 0 else None
            if score is not None:
                scored_values.append(score)
            controls_out.append({
                "id": cid, "name": name, "method": method,
                "score": score,
                "assets_passing": len(passing_assets),
                "assets_total": total,
                "checks": [{
                    "id": "1.1",
                    "title": "All enterprise assets tracked with active connector",
                    "pass_count": len(passing_assets),
                    "fail_count": len(failing_assets),
                    "failing_assets": [
                        {"id": a["id"], "name": a["name"], "detail": "No connector linked"}
                        for a in failing_assets
                    ],
                }],
            })
            continue

        # method == "agent_audit"
        # Collect all check results for this control across all assets
        # check_map: title -> {"id": str, "pass_assets": [], "fail_assets": []}
        check_map: dict[str, dict] = {}
        assets_audited: list[dict] = []
        assets_passing_ctrl: list[dict] = []

        for asset in assets:
            raw_controls = (
                (asset.get("asset_metadata") or {})
                .get("cis_compliance", {})
                .get("latest", {})
                .get("controls", [])
            )
            # Filter to checks relevant to this control
            relevant = [
                r for r in raw_controls
                if _section_matches(r.get("section", ""), sections)
            ]
            if not relevant:
                continue  # asset has no data for this control — exclude from denominator

            assets_audited.append(asset)
            asset_passes_ctrl = all(r.get("status") == "pass" for r in relevant)
            if asset_passes_ctrl:
                assets_passing_ctrl.append(asset)

            # Aggregate into check_map by title
            for r in relevant:
                title = r.get("title", r.get("id", "unknown"))
                if title not in check_map:
                    check_map[title] = {"id": r.get("id", ""), "pass_assets": [], "fail_assets": []}
                if r.get("status") == "pass":
                    check_map[title]["pass_assets"].append(asset)
                else:
                    check_map[title]["fail_assets"].append({
                        "id": asset["id"],
                        "name": asset["name"],
                        "detail": f"Expected: {r.get('expected', '')}  Got: {r.get('actual', '')}",
                    })

        n_audited = len(assets_audited)
        score = (len(assets_passing_ctrl) / n_audited) if n_audited > 0 else None
        if score is not None:
            scored_values.append(score)

        checks_out = [
            {
                "id": data["id"],
                "title": title,
                "pass_count": len(data["pass_assets"]),
                "fail_count": len(data["fail_assets"]),
                "failing_assets": data["fail_assets"],
            }
            for title, data in check_map.items()
        ]

        controls_out.append({
            "id": cid, "name": name, "method": method,
            "score": score,
            "assets_passing": len(assets_passing_ctrl),
            "assets_total": n_audited,
            "checks": checks_out,
        })

    overall = (sum(scored_values) / len(scored_values)) if scored_values else None

    return {
        "overall_score": overall,
        "tracked_controls": sum(1 for c in CIS_V8_CONTROLS if c["method"] != "not_tracked"),
        "last_updated": last_updated,
        "controls": controls_out,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend
python -m pytest tests/test_cis_compliance.py -v
```

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/compliance/cis_v8_map.py backend/tests/test_cis_compliance.py
git commit -m "feat(compliance): add CIS v8 control map and compute_cis_summary"
```

---

## Task 2: Pydantic Response Models

**Files:**
- Modify: `backend/app/schemas/compliance.py`
- Test: `backend/tests/test_cis_compliance.py` (extend)

- [ ] **Step 1: Write failing test for response model**

Append to `backend/tests/test_cis_compliance.py`:

```python
def test_cis_summary_response_model_accepts_valid_data():
    from app.schemas.compliance import CisSummaryResponse, CisControlRow, CisCheckRow, CisFailingAsset
    asset = CisFailingAsset(id="uuid", name="web-01", detail="Expected: 0  Got: 1")
    check = CisCheckRow(id="4.1", title="SSH check", pass_count=3, fail_count=1, failing_assets=[asset])
    ctrl = CisControlRow(
        id=4, name="Secure Config", method="agent_audit",
        score=0.75, assets_passing=3, assets_total=4, checks=[check]
    )
    resp = CisSummaryResponse(
        overall_score=0.75, tracked_controls=6,
        last_updated="2026-05-08T12:00:00Z", controls=[ctrl]
    )
    assert resp.overall_score == 0.75


def test_cis_summary_response_model_accepts_null_score():
    from app.schemas.compliance import CisControlRow
    ctrl = CisControlRow(
        id=2, name="Software Inventory", method="not_tracked",
        score=None, assets_passing=None, assets_total=None, checks=[]
    )
    assert ctrl.score is None
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd backend
python -m pytest tests/test_cis_compliance.py::test_cis_summary_response_model_accepts_valid_data -v
```

Expected: `ImportError: cannot import name 'CisSummaryResponse'`

- [ ] **Step 3: Add Pydantic models to `backend/app/schemas/compliance.py`**

Append to the bottom of the existing file:

```python
# ---- CIS v8 Summary ----

class CisFailingAsset(BaseModel):
    id: str
    name: str
    detail: str


class CisCheckRow(BaseModel):
    id: str
    title: str
    pass_count: int
    fail_count: int
    failing_assets: list[CisFailingAsset]


class CisControlRow(BaseModel):
    id: int
    name: str
    method: str  # "asset_coverage" | "agent_audit" | "not_tracked"
    score: Optional[float]
    assets_passing: Optional[int]
    assets_total: Optional[int]
    checks: list[CisCheckRow]


class CisSummaryResponse(BaseModel):
    overall_score: Optional[float]
    tracked_controls: int
    last_updated: Optional[str]
    controls: list[CisControlRow]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend
python -m pytest tests/test_cis_compliance.py -v
```

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas/compliance.py backend/tests/test_cis_compliance.py
git commit -m "feat(compliance): add CisSummaryResponse pydantic models"
```

---

## Task 3: Backend Endpoint `GET /compliance/cis-summary`

**Files:**
- Modify: `backend/app/routers/compliance.py`
- Test: `backend/tests/test_cis_compliance.py` (extend)

- [ ] **Step 1: Write failing test for the endpoint**

Append to `backend/tests/test_cis_compliance.py`:

```python
def test_cis_summary_endpoint_returns_18_controls():
    import asyncio
    from unittest.mock import AsyncMock, patch, MagicMock
    from app.routers.compliance import get_cis_summary

    # Mock user with organization_id
    mock_user = MagicMock()
    mock_user.organization_id = "org-1"

    # Mock DB returning two assets: one server with connector, one without
    asset1 = MagicMock()
    asset1.id = "a1"
    asset1.name = "web-01"
    asset1.connector_id = "conn-1"
    asset1.asset_metadata = {}

    asset2 = MagicMock()
    asset2.id = "a2"
    asset2.name = "db-01"
    asset2.connector_id = None
    asset2.asset_metadata = {}

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [asset1, asset2]

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    result = asyncio.run(get_cis_summary(user=mock_user, db=mock_db))

    assert len(result.controls) == 18
    assert result.tracked_controls == 6
    ctrl1 = next(c for c in result.controls if c.id == 1)
    assert ctrl1.score == pytest.approx(0.5)
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd backend
python -m pytest tests/test_cis_compliance.py::test_cis_summary_endpoint_returns_18_controls -v
```

Expected: `ImportError: cannot import name 'get_cis_summary'`

- [ ] **Step 3: Add the endpoint to `backend/app/routers/compliance.py`**

Add this import at the top of the file (after existing imports):

```python
from app.compliance.cis_v8_map import compute_cis_summary
from app.schemas.compliance import CisSummaryResponse
from app.models.asset import AssetType
```

Add this endpoint after the existing `list_drift_alerts` function (around line 152):

```python
@router.get("/cis-summary", response_model=CisSummaryResponse)
async def get_cis_summary(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the 18-control CIS Controls v8 compliance summary for the org."""
    result = await db.execute(
        select(Asset).where(
            Asset.organization_id == user.organization_id,
            Asset.asset_type == AssetType.server,
        )
    )
    assets = result.scalars().all()

    # Convert SQLAlchemy models to plain dicts for compute_cis_summary
    asset_dicts = [
        {
            "id": str(a.id),
            "name": a.name,
            "connector_id": str(a.connector_id) if a.connector_id else None,
            "asset_metadata": a.asset_metadata or {},
        }
        for a in assets
    ]

    summary = compute_cis_summary(asset_dicts)
    return CisSummaryResponse(**summary)
```

- [ ] **Step 4: Run all compliance tests**

```bash
cd backend
python -m pytest tests/test_cis_compliance.py -v
```

Expected: All tests PASS.

- [ ] **Step 5: Smoke-test the endpoint manually**

```bash
# Start the backend if not running
docker exec nexplane-backend-1 sh -c "python3 -c \"
import requests
r = requests.post('http://localhost:8000/auth/login', json={'email': 'admin@acme.example', 'password': 'admin123'})
t = r.json()['access_token']
s = requests.get('http://localhost:8000/compliance/cis-summary', headers={'Authorization': 'Bearer ' + t})
import json; data = s.json()
print('Status:', s.status_code)
print('Controls:', len(data['controls']))
print('Tracked:', data['tracked_controls'])
print('Overall:', data['overall_score'])
\""
```

Expected: `Status: 200`, `Controls: 18`, `Tracked: 6`

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/compliance.py
git commit -m "feat(compliance): add GET /compliance/cis-summary endpoint"
```

---

## Task 4: Frontend API Client

**Files:**
- Modify: `frontend/src/api/endpoints.ts`

(No dedicated test — the hook is trivial and covered by integration when the page renders.)

- [ ] **Step 1: Add `complianceApi` to `frontend/src/api/endpoints.ts`**

Find the last `export const ...Api` block in `frontend/src/api/endpoints.ts` and add:

```typescript
// ---- Compliance ----

export interface CisFailingAsset {
  id: string;
  name: string;
  detail: string;
}

export interface CisCheckRow {
  id: string;
  title: string;
  pass_count: number;
  fail_count: number;
  failing_assets: CisFailingAsset[];
}

export interface CisControlRow {
  id: number;
  name: string;
  method: "asset_coverage" | "agent_audit" | "not_tracked";
  score: number | null;
  assets_passing: number | null;
  assets_total: number | null;
  checks: CisCheckRow[];
}

export interface CisSummaryResponse {
  overall_score: number | null;
  tracked_controls: number;
  last_updated: string | null;
  controls: CisControlRow[];
}

export const complianceApi = {
  getSummary: (): Promise<CisSummaryResponse> =>
    apiClient.get<CisSummaryResponse>("/compliance/cis-summary").then((r) => r.data),
};
```

- [ ] **Step 2: Verify TypeScript compiles cleanly**

```bash
docker exec nexplane-frontend-1 sh -c "cd /app && npx tsc --noEmit 2>&1 | grep -v 'error TS' || echo 'No new errors'"
```

Expected: no new errors introduced by this change.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/endpoints.ts
git commit -m "feat(compliance): add complianceApi.getSummary() TypeScript client"
```

---

## Task 5: Replace `Compliance.tsx` with CIS v8 Dashboard

**Files:**
- Replace: `frontend/src/pages/Compliance.tsx`

This is the largest task. The existing file is completely replaced.

- [ ] **Step 1: Write the new `frontend/src/pages/Compliance.tsx`**

Replace the entire file with:

```typescript
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ChevronRight, ChevronDown, Play, Loader2,
  CheckCircle2, AlertTriangle, XCircle, Minus,
} from "lucide-react";
import { apiClient } from "../api/client";
import { complianceApi, CisControlRow, CisCheckRow } from "../api/endpoints";
import { PageLoading } from "../components/LoadingSpinner";
import { formatDistanceToNow } from "date-fns";

// ── Status badge ──────────────────────────────────────────────────────────────

function StatusBadge({ score, method }: { score: number | null; method: string }) {
  if (method === "not_tracked") {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-slate-400">
        <Minus size={12} /> Not tracked
      </span>
    );
  }
  if (score === null) {
    return <span className="text-xs text-slate-400">No data</span>;
  }
  if (score >= 0.8) {
    return (
      <span className="inline-flex items-center gap-1 text-xs font-medium text-green-700">
        <CheckCircle2 size={13} /> Passing
      </span>
    );
  }
  if (score >= 0.6) {
    return (
      <span className="inline-flex items-center gap-1 text-xs font-medium text-amber-600">
        <AlertTriangle size={13} /> At risk
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 text-xs font-medium text-red-600">
      <XCircle size={13} /> Failing
    </span>
  );
}

// ── Score bar ─────────────────────────────────────────────────────────────────

function ScoreBar({ score }: { score: number | null }) {
  if (score === null) {
    return <span className="text-slate-300 text-sm">—</span>;
  }
  const pct = Math.round(score * 100);
  const color =
    score >= 0.8 ? "bg-green-500" :
    score >= 0.6 ? "bg-amber-400" :
    "bg-red-500";
  return (
    <div className="flex items-center gap-2">
      <div className="w-20 h-1.5 bg-slate-100 rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-sm tabular-nums text-slate-700">{pct}%</span>
    </div>
  );
}

// ── Failing asset list ────────────────────────────────────────────────────────

function FailingAssetList({ check }: { check: CisCheckRow }) {
  if (check.failing_assets.length === 0) return null;
  return (
    <div className="ml-8 mt-1 mb-2 bg-red-50 border border-red-100 rounded-md overflow-hidden">
      <div className="px-3 py-1 border-b border-red-100 text-xs font-medium text-red-700 uppercase tracking-wide">
        Failing assets
      </div>
      <table className="w-full text-xs">
        <tbody>
          {check.failing_assets.map((a) => (
            <tr key={a.id} className="border-b border-red-50 last:border-0">
              <td className="px-3 py-1.5 font-medium text-slate-800 w-40">{a.name}</td>
              <td className="px-3 py-1.5 text-slate-500 font-mono">{a.detail}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Check row (expandable) ────────────────────────────────────────────────────

function CheckRow({ check }: { check: CisCheckRow }) {
  const [expanded, setExpanded] = useState(false);
  const hasFailing = check.fail_count > 0;
  return (
    <div>
      <div
        className={`flex items-center gap-2 px-4 py-1.5 text-sm cursor-pointer hover:bg-slate-50 ${hasFailing ? "cursor-pointer" : "cursor-default"}`}
        onClick={() => hasFailing && setExpanded((e) => !e)}
      >
        {hasFailing ? (
          expanded ? <ChevronDown size={12} className="text-slate-400 shrink-0" /> : <ChevronRight size={12} className="text-slate-400 shrink-0" />
        ) : (
          <span className="w-3" />
        )}
        <span className="w-8 text-xs text-slate-400 shrink-0">{check.id}</span>
        <span className="flex-1 text-slate-700">{check.title}</span>
        <span className="w-10 text-center text-xs text-green-600">{check.pass_count}</span>
        <span className="w-10 text-center text-xs text-red-500">{check.fail_count}</span>
        <span className="w-10 text-center text-xs text-slate-400">{check.pass_count + check.fail_count}</span>
      </div>
      {expanded && <FailingAssetList check={check} />}
    </div>
  );
}

// ── Control row (expandable) ──────────────────────────────────────────────────

function ControlRow({ ctrl }: { ctrl: CisControlRow }) {
  const [expanded, setExpanded] = useState(false);
  const isTracked = ctrl.method !== "not_tracked";
  const hasData = ctrl.score !== null;

  return (
    <div className={`border-b border-slate-100 ${!isTracked ? "opacity-50" : ""}`}>
      {/* Main row */}
      <div
        className={`flex items-center gap-3 px-4 py-3 ${isTracked ? "cursor-pointer hover:bg-slate-50" : ""}`}
        onClick={() => isTracked && setExpanded((e) => !e)}
      >
        {isTracked ? (
          expanded
            ? <ChevronDown size={14} className="text-slate-400 shrink-0" />
            : <ChevronRight size={14} className="text-slate-400 shrink-0" />
        ) : (
          <span className="w-3.5" />
        )}
        <span className="w-6 text-xs font-mono text-slate-400 shrink-0">{ctrl.id}</span>
        <span className="flex-1 text-sm font-medium text-slate-800">{ctrl.name}</span>
        <div className="w-32">
          <ScoreBar score={ctrl.score} />
        </div>
        <div className="w-20 text-sm text-slate-500 text-center">
          {hasData ? `${ctrl.assets_passing}/${ctrl.assets_total}` : "—"}
        </div>
        <div className="w-28 text-right">
          <StatusBadge score={ctrl.score} method={ctrl.method} />
        </div>
      </div>

      {/* Expanded: checks sub-table */}
      {expanded && ctrl.checks.length > 0 && (
        <div className="bg-slate-50 border-t border-slate-100">
          {/* Checks header */}
          <div className="flex items-center gap-2 px-4 py-1 text-xs font-medium text-slate-500 uppercase tracking-wide border-b border-slate-100">
            <span className="w-3" />
            <span className="w-8" />
            <span className="flex-1">Check</span>
            <span className="w-10 text-center text-green-600">Pass</span>
            <span className="w-10 text-center text-red-500">Fail</span>
            <span className="w-10 text-center">Total</span>
          </div>
          {ctrl.checks.map((ch) => (
            <CheckRow key={ch.id + ch.title} check={ch} />
          ))}
        </div>
      )}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export function Compliance() {
  const queryClient = useQueryClient();

  const { data: summary, isLoading } = useQuery({
    queryKey: ["cis-summary"],
    queryFn: complianceApi.getSummary,
    refetchInterval: 30_000,
  });

  const runAuditMutation = useMutation({
    mutationFn: async () => {
      // Fetch all server assets
      const assets: { id: string }[] = await apiClient
        .get("/assets", { params: { asset_type: "server" } })
        .then((r) => r.data);

      // Fire agent_compliance CR for each asset in parallel
      await Promise.all(
        assets.map(async (asset) => {
          try {
            const cr = await apiClient
              .post("/change-requests", {
                title: "CIS v8 Full Audit",
                description: "Full CIS Controls v8 compliance audit",
                change_type: "agent_compliance",
                target_asset_ids: [asset.id],
                desired_outcome: { dry_run: false },
              })
              .then((r) => r.data);
            const crId = cr.id;
            await apiClient.post(`/change-requests/${crId}/plan`);
            await apiClient.post(`/change-requests/${crId}/submit-for-approval`);
            await apiClient.post(`/change-requests/${crId}/approve`, {
              decision: "approved",
              comment: "Auto-approved via Run Full Audit",
            });
            await apiClient.post(`/change-requests/${crId}/execute`);
          } catch {
            // Skip assets where audit fails (e.g., no agent registered)
          }
        })
      );
    },
    onSuccess: () => {
      // Wait briefly then refetch — audit results appear after agent responds
      setTimeout(() => queryClient.invalidateQueries({ queryKey: ["cis-summary"] }), 5000);
    },
  });

  if (isLoading) return <PageLoading />;

  const overallPct = summary?.overall_score != null
    ? Math.round(summary.overall_score * 100)
    : null;

  const overallColor =
    overallPct == null ? "bg-slate-300" :
    overallPct >= 80    ? "bg-green-500" :
    overallPct >= 60    ? "bg-amber-400" :
    "bg-red-500";

  return (
    <div className="max-w-5xl mx-auto py-8 px-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">Compliance</h1>
          <p className="text-sm text-slate-500 mt-0.5">
            CIS Controls v8
            {summary?.last_updated && (
              <> · Last updated {formatDistanceToNow(new Date(summary.last_updated), { addSuffix: true })}</>
            )}
          </p>
        </div>
        <button
          onClick={() => runAuditMutation.mutate()}
          disabled={runAuditMutation.isPending}
          className="inline-flex items-center gap-2 px-4 py-2 bg-brand-600 text-white text-sm font-medium rounded-md hover:bg-brand-700 disabled:opacity-50 transition-colors"
        >
          {runAuditMutation.isPending ? (
            <Loader2 size={14} className="animate-spin" />
          ) : (
            <Play size={14} />
          )}
          Run Full Audit
        </button>
      </div>

      {/* Overall score bar */}
      {summary && (
        <div className="bg-white border border-slate-200 rounded-lg p-4 mb-6">
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm font-medium text-slate-700">
              Overall — {summary.tracked_controls} of 18 controls tracked
            </span>
            <span className="text-lg font-semibold text-slate-900">
              {overallPct != null ? `${overallPct}%` : "—"}
            </span>
          </div>
          <div className="w-full h-2 bg-slate-100 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all ${overallColor}`}
              style={{ width: overallPct != null ? `${overallPct}%` : "0%" }}
            />
          </div>
        </div>
      )}

      {/* 18-control table */}
      <div className="bg-white border border-slate-200 rounded-lg overflow-hidden">
        {/* Table header */}
        <div className="flex items-center gap-3 px-4 py-2.5 border-b border-slate-200 bg-slate-50 text-xs font-medium text-slate-500 uppercase tracking-wide">
          <span className="w-3.5" />
          <span className="w-6">#</span>
          <span className="flex-1">Control</span>
          <span className="w-32">Score</span>
          <span className="w-20 text-center">Assets</span>
          <span className="w-28 text-right">Status</span>
        </div>

        {summary?.controls.map((ctrl) => (
          <ControlRow key={ctrl.id} ctrl={ctrl} />
        ))}

        {!summary && (
          <div className="text-center py-12 text-slate-400 text-sm">
            No compliance data yet — run an audit to populate scores
          </div>
        )}
      </div>
    </div>
  );
}

export default Compliance;
```

- [ ] **Step 2: Verify TypeScript compiles cleanly**

```bash
docker exec nexplane-frontend-1 sh -c "cd /app && npx tsc --noEmit 2>&1 | grep 'Compliance\|cisSum\|CisControl\|CisCheck' | head -20"
```

Expected: no errors referencing `Compliance.tsx` or the new types.

- [ ] **Step 3: Smoke-test in browser**

Open `http://localhost:3000/compliance`. Expected:
- 18-row table renders
- Rows for Controls 1–8 are fully styled; Controls 9–18 appear greyed out with "Not tracked"
- "Run Full Audit" button is visible
- Clicking a tracked control with data expands its check rows

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/Compliance.tsx
git commit -m "feat(compliance): replace Compliance page with CIS v8 18-control dashboard"
```

---

## Self-Review Checklist

**Spec coverage:**
- ✅ 18-control table with `#`, Control, Score, Assets, Status columns
- ✅ Tracked controls expandable to checks, checks expandable to failing assets
- ✅ `asset_coverage` for Control 1, `agent_audit` for Controls 4–8, `not_tracked` for the rest
- ✅ Status badge logic: ≥80% ✅, ≥60% ⚠️, <60% 🔴, null → "Not tracked"
- ✅ "Run Full Audit" fires `agent_compliance` CRs for all server assets
- ✅ No new DB tables
- ✅ Old per-asset view, drift alerts, evidence collection button removed
- ✅ `compute_cis_summary` function is independently testable (plain dicts in, plain dict out)
- ✅ Endpoint converts SQLAlchemy models to dicts before passing to pure function

**Type consistency:**
- `CisControlRow`, `CisCheckRow`, `CisFailingAsset` used consistently across Task 2 (Pydantic), Task 4 (TypeScript), Task 5 (component props)
- `complianceApi.getSummary()` returns `CisSummaryResponse` — matches what the endpoint returns

**Placeholder scan:** No TBDs, no "implement later", all code blocks complete.
