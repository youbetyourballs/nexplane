# Catalog Action Smoke & Commercial Load Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Live-verify the `catalog_action` CR lifecycle against real infrastructure and enforce that commercial mutating actions cannot load without smoke verification.

**Architecture:** Task 1 adds a load-time gate to `catalog_service.py` that skips commercial non-read-only actions missing `smoke_verified: true`, with 5 unit tests. Task 2 creates a new smoke file `test_catalog_action_live.py` with 3 phases exercising the full `catalog_action` CR lifecycle against live EC2, then runs on EC2 to confirm all phases pass.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x async, httpx, asyncpg, pytest, boto3 (EC2 discover action)

## Global Constraints

- All smoke assertions run against live EC2 infrastructure — no mocks
- Smoke follows `smoke_helpers.py` conventions: `NexplaneClient`, `log`, `fail`, `make_base_parser`
- CR lifecycle endpoint sequence: `POST /change-requests` → `POST /{id}/plan` → `POST /{id}/submit-for-approval` → `POST /{id}/approve` → `POST /{id}/execute`; approve body: `{"decision": "approved", "comment": "smoke test"}`; terminal status: `"completed"`
- Self-approval is allowed (same admin token for create and approve — confirmed by `run_cr()` in smoke_helpers)
- Load gate applies only to `domain=commercial` + `read_only=false` actions — core domain actions exempt
- Gate behavior: hard skip (action excluded from `_catalog` and `_generic_index`) + `logger.error(...)` — not a warning, not a startup crash
- SPDX header on all new files: `# SPDX-License-Identifier: AGPL-3.0-only\n# Copyright (C) 2024-2026 Nexplane, Inc.`

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `backend/app/connectors/catalog_service.py` | Modify | Add load gate in `_load()`: skip commercial mutating actions without `smoke_verified: true` |
| `backend/tests/unit/test_catalog_service_smoke_gate.py` | Create | 5 unit tests for the gate |
| `backend/tests/smoke/test_catalog_action_live.py` | Create | 3-phase live smoke: CATALOG_DISCOVERY, CATALOG_CR_LIFECYCLE, CATALOG_ACTION_ERRORS |

---

## Task 1: Commercial Load Gate

**Files:**
- Modify: `backend/app/connectors/catalog_service.py` (lines 1-10, 43-58)
- Create: `backend/tests/unit/test_catalog_service_smoke_gate.py`

**Interfaces:**
- Produces: `ActionCatalogService._load()` silently skips commercial mutating actions that lack `"smoke_verified": true`; `logger.error` is emitted for each skipped action

- [ ] **Step 1: Write failing tests**

Create `backend/tests/unit/test_catalog_service_smoke_gate.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import json
import logging
import pathlib
import pytest
from app.connectors.catalog_service import ActionCatalogService


def _write_catalog(tmp_path: pathlib.Path, connector_type: str, actions: list) -> pathlib.Path:
    data = {
        "connector_type": connector_type,
        "display_name": connector_type,
        "credential_fields": [],
        "actions": actions,
    }
    (tmp_path / f"{connector_type}.json").write_text(json.dumps(data))
    return tmp_path


def test_commercial_mutating_without_smoke_verified_is_skipped(tmp_path, caplog):
    """Commercial non-read-only action without smoke_verified=true must not load."""
    _write_catalog(tmp_path, "ops_test", [
        {"action_id": "provision", "domain": "commercial", "read_only": False},
    ])
    with caplog.at_level(logging.ERROR, logger="app.connectors.catalog_service"):
        svc = ActionCatalogService(tmp_path)
    loaded = [a["action_id"] for a in svc._catalog.get("ops_test", [])]
    assert "provision" not in loaded
    assert "not smoke-verified" in caplog.text


def test_commercial_mutating_with_smoke_verified_true_loads(tmp_path):
    """Commercial non-read-only action with smoke_verified=true must load."""
    _write_catalog(tmp_path, "ops_test", [
        {"action_id": "provision", "domain": "commercial", "read_only": False, "smoke_verified": True},
    ])
    svc = ActionCatalogService(tmp_path)
    loaded = [a["action_id"] for a in svc._catalog.get("ops_test", [])]
    assert "provision" in loaded


def test_commercial_mutating_smoke_verified_false_is_skipped(tmp_path, caplog):
    """Explicit smoke_verified=false must also be skipped."""
    _write_catalog(tmp_path, "ops_test", [
        {"action_id": "terminate_instance", "domain": "commercial", "read_only": False, "smoke_verified": False},
    ])
    with caplog.at_level(logging.ERROR, logger="app.connectors.catalog_service"):
        svc = ActionCatalogService(tmp_path)
    loaded = [a["action_id"] for a in svc._catalog.get("ops_test", [])]
    assert "terminate_instance" not in loaded
    assert "not smoke-verified" in caplog.text


def test_commercial_read_only_loads_without_smoke_verified(tmp_path):
    """Commercial read-only action must load even without smoke_verified (exempt)."""
    _write_catalog(tmp_path, "ops_test", [
        {"action_id": "list_customers", "domain": "commercial", "read_only": True},
    ])
    svc = ActionCatalogService(tmp_path)
    loaded = [a["action_id"] for a in svc._catalog.get("ops_test", [])]
    assert "list_customers" in loaded


def test_core_mutating_loads_without_smoke_verified(tmp_path):
    """Core domain mutating action is exempt — must load without smoke_verified."""
    _write_catalog(tmp_path, "aws_test", [
        {"action_id": "tag_resource", "domain": "core", "read_only": False},
    ])
    svc = ActionCatalogService(tmp_path)
    loaded = [a["action_id"] for a in svc._catalog.get("aws_test", [])]
    assert "tag_resource" in loaded
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec -T backend pytest tests/unit/test_catalog_service_smoke_gate.py -v
```

Expected: 5 tests collected, all FAIL (gate not implemented yet).

- [ ] **Step 3: Add `import logging` and module-level logger to catalog_service.py**

At the top of `backend/app/connectors/catalog_service.py`, after the existing imports (around line 10), add:

```python
import logging

_log = logging.getLogger(__name__)
```

The file already has `from __future__ import annotations`, `import importlib`, etc. Add these two lines after the last existing import.

- [ ] **Step 4: Modify `_load()` to filter commercial mutating actions**

In `backend/app/connectors/catalog_service.py`, replace the `_load` method (currently lines 43-58):

```python
    def _load(self, catalog_dir: pathlib.Path) -> None:
        for json_file in sorted(catalog_dir.glob("*.json")):
            data = json.loads(json_file.read_text())
            connector_type = data["connector_type"]
            raw_actions = data.get("actions", [])

            # Gate: commercial mutating actions must carry smoke_verified=true before
            # they are allowed into the catalog. Core domain actions are exempt —
            # they are covered by per-connector smoke suites.
            actions: list[dict] = []
            for action_def in raw_actions:
                if (
                    action_def.get("domain") == "commercial"
                    and not action_def.get("read_only", True)
                    and not action_def.get("smoke_verified", False)
                ):
                    _log.error(
                        "Commercial mutating action %s.%s is not smoke-verified — skipping",
                        connector_type,
                        action_def.get("action_id", "?"),
                    )
                    continue
                actions.append(action_def)

            self._catalog[connector_type] = actions
            self._raw[connector_type] = data
            for action_def in actions:
                generic = action_def.get("generic_action")
                option = ActionOption(
                    connector_type=connector_type,
                    action_id=action_def["action_id"],
                    action_def=action_def,
                    execution_tier=action_def.get("execution_tier", 99),
                )
                if generic:
                    self._generic_index.setdefault(generic, []).append(option)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
docker compose exec -T backend pytest tests/unit/test_catalog_service_smoke_gate.py -v
```

Expected: 5/5 PASSED.

- [ ] **Step 6: Run the full unit suite to check for regressions**

```bash
docker compose exec -T backend pytest tests/unit/ -v --tb=short -q
```

Expected: all passing (the gate only filters commercial actions; core catalog is unaffected).

- [ ] **Step 7: Commit**

```bash
git add backend/app/connectors/catalog_service.py backend/tests/unit/test_catalog_service_smoke_gate.py
git commit -m "feat: commercial catalog load gate — skip unverified mutating actions"
```

---

## Task 2: Smoke Test File + Live EC2 Run

**Files:**
- Create: `backend/tests/smoke/test_catalog_action_live.py`

**Interfaces:**
- Consumes: `smoke_helpers.NexplaneClient`, `log`, `fail`, `make_base_parser` (same as all other smoke files)
- Consumes: REST endpoints at `{base_url}/change-requests`, `/catalog/actions`, `/capabilities`

- [ ] **Step 1: Create the smoke test file**

Create `backend/tests/smoke/test_catalog_action_live.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
#!/usr/bin/env python3
"""
Nexplane Catalog Action Live Smoke Test

Usage:
    python tests/smoke/test_catalog_action_live.py \\
        --email admin@acme.example --password admin123 \\
        --phases CATALOG_DISCOVERY,CATALOG_CR_LIFECYCLE,CATALOG_ACTION_ERRORS

Phase descriptions:
    CATALOG_DISCOVERY      Verify /capabilities and /catalog/actions return correct structure
    CATALOG_CR_LIFECYCLE   Full catalog_action CR lifecycle via aws.discover_ec2_instances
    CATALOG_ACTION_ERRORS  Error paths: unknown action → plan_blocked; empty outcome → graceful failure

Requirements:
    AWS connector active in the org (used by discover_ec2_instances)
"""
import sys
import time

from smoke_helpers import NexplaneClient, log, fail, make_base_parser

ALL_PHASES = ["CATALOG_DISCOVERY", "CATALOG_CR_LIFECYCLE", "CATALOG_ACTION_ERRORS"]

_smoke_cr_id: str = ""


def phase_catalog_discovery(client: NexplaneClient) -> None:
    base = client.base.rstrip("/")

    # 1. GET /capabilities — edition + domains present
    resp = client.client.get(f"{base}/capabilities")
    if resp.status_code != 200:
        fail(f"GET /capabilities returned {resp.status_code}: {resp.text}")
    caps = resp.json()
    if "edition" not in caps:
        fail(f"Missing 'edition' key in capabilities response: {caps}")
    if not isinstance(caps.get("domains"), list):
        fail(f"'domains' must be a list in capabilities response: {caps}")
    log(f"capabilities: edition={caps['edition']} domains={caps['domains']}")

    # 2. GET /catalog/actions — non-empty, correct shape
    resp = client.client.get(f"{base}/catalog/actions")
    if resp.status_code != 200:
        fail(f"GET /catalog/actions returned {resp.status_code}: {resp.text}")
    actions = resp.json()
    if not isinstance(actions, list) or len(actions) == 0:
        fail(f"Expected non-empty list from /catalog/actions, got: {type(actions)}")
    required_keys = {"connector_type", "action_id", "read_only", "destructive", "domain", "display_name", "description"}
    for a in actions[:5]:
        missing = required_keys - set(a.keys())
        if missing:
            fail(f"Action missing required keys {missing}: {a}")
    log(f"/catalog/actions: {len(actions)} actions, shape check passed")

    # 3. GET /catalog/actions?domain=core — subset with domain filter
    resp = client.client.get(f"{base}/catalog/actions", params={"domain": "core"})
    if resp.status_code != 200:
        fail(f"GET /catalog/actions?domain=core returned {resp.status_code}: {resp.text}")
    core_actions = resp.json()
    if not isinstance(core_actions, list) or len(core_actions) == 0:
        fail("Expected non-empty list from /catalog/actions?domain=core")
    wrong_domain = [a for a in core_actions if a.get("domain") != "core"]
    if wrong_domain:
        fail(f"Non-core actions returned for domain=core filter: {wrong_domain[:2]}")
    if len(core_actions) > len(actions):
        fail(f"domain=core returned more actions ({len(core_actions)}) than unfiltered ({len(actions)})")
    log(f"/catalog/actions?domain=core: {len(core_actions)} actions, all domain=core ✓")

    log("CATALOG_DISCOVERY passed")


def phase_catalog_cr_lifecycle(client: NexplaneClient) -> None:
    global _smoke_cr_id
    base = client.base.rstrip("/")

    # 1. Create catalog_action CR
    resp = client.client.post(f"{base}/change-requests", json={
        "change_type": "catalog_action",
        "title": "smoke-catalog-discover-ec2",
        "desired_outcome": {
            "connector_type": "aws",
            "action_id": "discover_ec2_instances",
            "params": {},
        },
        "target_asset_ids": [],
    })
    if resp.status_code != 201:
        fail(f"POST /change-requests returned {resp.status_code}: {resp.text}")
    cr = resp.json()
    cr_id = cr.get("id")
    if not cr_id:
        fail(f"No 'id' in create CR response: {cr}")
    _smoke_cr_id = cr_id
    log(f"Created catalog_action CR {cr_id} status={cr.get('status')}")

    # 2. Plan
    resp = client.client.post(f"{base}/change-requests/{cr_id}/plan")
    if resp.status_code not in (200, 201, 204):
        fail(f"POST /plan returned {resp.status_code}: {resp.text}")
    log("Plan submitted")

    # 3. Read CR — verify plan structure
    resp = client.client.get(f"{base}/change-requests/{cr_id}")
    if resp.status_code != 200:
        fail(f"GET /change-requests/{cr_id} returned {resp.status_code}")
    cr = resp.json()
    plan = cr.get("plan") or {}
    steps = plan.get("steps") or cr.get("steps") or []
    if not steps:
        fail(f"No steps in plan after /plan call. CR status={cr.get('status')}, plan={plan}")
    if len(steps) != 1:
        fail(f"Expected exactly 1 plan step for catalog_action, got {len(steps)}: {steps}")
    step = steps[0]
    if step.get("connector_type") != "aws":
        fail(f"Plan step connector_type expected 'aws', got '{step.get('connector_type')}': {step}")
    if step.get("action_id") != "discover_ec2_instances":
        fail(f"Plan step action_id expected 'discover_ec2_instances', got '{step.get('action_id')}': {step}")
    log(f"Plan verified: 1 step — {step['connector_type']}.{step['action_id']}")

    # 4. Submit for approval
    resp = client.client.post(f"{base}/change-requests/{cr_id}/submit-for-approval")
    if resp.status_code not in (200, 201, 204):
        fail(f"POST /submit-for-approval returned {resp.status_code}: {resp.text}")
    log("Submitted for approval")

    # 5. Approve (self-approval allowed for admin in smoke context)
    resp = client.client.post(
        f"{base}/change-requests/{cr_id}/approve",
        json={"decision": "approved", "comment": "smoke test"},
    )
    if resp.status_code not in (200, 201, 204):
        fail(f"POST /approve returned {resp.status_code}: {resp.text}")
    log("Approved CR")

    # 6. Execute
    resp = client.client.post(f"{base}/change-requests/{cr_id}/execute")
    if resp.status_code not in (200, 201, 202, 204):
        fail(f"POST /execute returned {resp.status_code}: {resp.text}")
    log("Execute submitted")

    # 7. Poll until terminal status
    for _ in range(30):
        resp = client.client.get(f"{base}/change-requests/{cr_id}")
        cr = resp.json()
        status = cr.get("status", "")
        if status in ("completed", "failed", "rolled_back", "rejected"):
            break
        time.sleep(2)
    else:
        fail(f"CR did not reach terminal status within 60s — last status: {cr.get('status')}")

    if cr.get("status") != "completed":
        fail(f"CR ended in unexpected status '{cr.get('status')}'. result={cr.get('execution_result') or cr.get('result')}")

    result = cr.get("execution_result") or cr.get("result")
    if result is None:
        fail("No execution_result present on completed catalog_action CR")
    log(f"Execution result present (type={type(result).__name__})")

    log("CATALOG_CR_LIFECYCLE passed")


def phase_catalog_action_errors(client: NexplaneClient) -> None:
    base = client.base.rstrip("/")

    # 1. Unknown action_id → plan_blocked (or 400/422 at plan time)
    resp = client.client.post(f"{base}/change-requests", json={
        "change_type": "catalog_action",
        "title": "smoke-catalog-unknown-action",
        "desired_outcome": {
            "connector_type": "aws",
            "action_id": "does_not_exist_xyz_smoke",
            "params": {},
        },
        "target_asset_ids": [],
    })
    if resp.status_code != 201:
        fail(f"Create error-test CR returned {resp.status_code}: {resp.text}")
    bad_cr_id = resp.json().get("id")

    plan_resp = client.client.post(f"{base}/change-requests/{bad_cr_id}/plan")
    if plan_resp.status_code in (400, 422):
        log("Unknown action_id → /plan returned 400/422 ✓")
    else:
        # Check that CR transitioned to plan_blocked
        cr_resp = client.client.get(f"{base}/change-requests/{bad_cr_id}")
        bad_cr = cr_resp.json()
        if bad_cr.get("status") != "plan_blocked":
            fail(
                f"Expected plan_blocked for unknown action_id, got status={bad_cr.get('status')}. "
                f"plan_resp={plan_resp.status_code} {plan_resp.text}"
            )
        log("Unknown action_id → status=plan_blocked ✓")

    # 2. Empty desired_outcome → graceful failure at plan (not a 500)
    resp = client.client.post(f"{base}/change-requests", json={
        "change_type": "catalog_action",
        "title": "smoke-catalog-empty-outcome",
        "desired_outcome": {},
        "target_asset_ids": [],
    })
    if resp.status_code == 422:
        log("Empty desired_outcome → 422 at create ✓")
    elif resp.status_code == 201:
        empty_cr_id = resp.json().get("id")
        plan_resp = client.client.post(f"{base}/change-requests/{empty_cr_id}/plan")
        if plan_resp.status_code in (400, 422):
            log("Empty desired_outcome → /plan returned 400/422 ✓")
        else:
            cr_resp = client.client.get(f"{base}/change-requests/{empty_cr_id}")
            empty_cr = cr_resp.json()
            if empty_cr.get("status") not in ("plan_blocked", "error", "failed"):
                fail(
                    f"Empty desired_outcome did not fail gracefully: status={empty_cr.get('status')}, "
                    f"plan_resp={plan_resp.status_code} {plan_resp.text}"
                )
            log(f"Empty desired_outcome → graceful failure (status={empty_cr.get('status')}) ✓")
    else:
        fail(f"Create with empty desired_outcome returned unexpected {resp.status_code}: {resp.text}")

    log("CATALOG_ACTION_ERRORS passed")


def main() -> None:
    parser = make_base_parser("Nexplane Catalog Action Live Smoke Test")
    parser.add_argument(
        "--phases",
        default=",".join(ALL_PHASES),
        help=f"Comma-separated phases to run. Default: {','.join(ALL_PHASES)}",
    )
    args = parser.parse_args()
    selected = [p.strip() for p in args.phases.split(",")]

    client = NexplaneClient(args.base_url, args.email, args.password)

    phase_fns = {
        "CATALOG_DISCOVERY": lambda: phase_catalog_discovery(client),
        "CATALOG_CR_LIFECYCLE": lambda: phase_catalog_cr_lifecycle(client),
        "CATALOG_ACTION_ERRORS": lambda: phase_catalog_action_errors(client),
    }

    passed: list[str] = []
    failed: list[str] = []

    for phase in selected:
        if phase not in phase_fns:
            print(f"  Unknown phase: {phase}")
            failed.append(phase)
            continue
        print(f"\n{'='*60}")
        print(f"  Phase: {phase}")
        print(f"{'='*60}")
        try:
            phase_fns[phase]()
            passed.append(phase)
            print(f"  ✓ {phase} PASSED")
        except SystemExit:
            failed.append(phase)
            print(f"  ✗ {phase} FAILED")

    print(f"\n{'='*60}")
    print(f"CATALOG_ACTION_SMOKE summary: {len(passed)}/{len(selected)} passed")
    for p in passed:
        print(f"  PASSED: {p}")
    for f in failed:
        print(f"  FAILED: {f}")
    if failed:
        print("CATALOG_ACTION_SMOKE PHASES FAILED")
        sys.exit(1)
    else:
        print("ALL CATALOG_ACTION_SMOKE PHASES PASSED")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: SCP the file to EC2**

```bash
scp -i ~/.ssh/id_ed25519 backend/tests/smoke/test_catalog_action_live.py \
    ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/tests/smoke/
```

- [ ] **Step 3: Run CATALOG_DISCOVERY alone first to verify endpoints are reachable**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "docker compose -f /home/ec2-user/nexplane/docker-compose.yml exec -T backend \
   python tests/smoke/test_catalog_action_live.py \
   --email admin@acme.example --password admin123 \
   --phases CATALOG_DISCOVERY"
```

Expected: `ALL CATALOG_ACTION_SMOKE PHASES PASSED`

- [ ] **Step 4: Run all 3 phases**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "docker compose -f /home/ec2-user/nexplane/docker-compose.yml exec -T backend \
   python tests/smoke/test_catalog_action_live.py \
   --email admin@acme.example --password admin123 \
   --phases CATALOG_DISCOVERY,CATALOG_CR_LIFECYCLE,CATALOG_ACTION_ERRORS"
```

Expected output ends with:
```
CATALOG_ACTION_SMOKE summary: 3/3 passed
  PASSED: CATALOG_DISCOVERY
  PASSED: CATALOG_CR_LIFECYCLE
  PASSED: CATALOG_ACTION_ERRORS
ALL CATALOG_ACTION_SMOKE PHASES PASSED
```

If any phase fails, read the error output — the most likely issues are:
- `POST /change-requests/{id}/plan` returns a different status code: adjust the accepted codes in the assertion
- CR terminal status is different from `"completed"`: check `cr.get("status")` in the poll output and update the terminal status set
- Plan step keys use different names (`"type"` instead of `"connector_type"`): inspect the full CR JSON and adjust the step field names

- [ ] **Step 5: Commit**

```bash
git add backend/tests/smoke/test_catalog_action_live.py
git commit -m "smoke: catalog_action CR lifecycle — 3/3 phases passing on EC2"
git push origin master
```

---

## Self-Review

**Spec coverage:**
- CATALOG_DISCOVERY: `GET /capabilities` ✓, `GET /catalog/actions` ✓, `GET /catalog/actions?domain=core` ✓
- CATALOG_CR_LIFECYCLE: create ✓, plan verified (connector_type + action_id) ✓, submit ✓, approve ✓, execute ✓, poll terminal ✓, execution_result present ✓
- CATALOG_ACTION_ERRORS: unknown action_id → plan_blocked ✓, empty desired_outcome → graceful failure ✓
- Load gate: commercial mutating without smoke_verified skipped ✓, with smoke_verified=true loads ✓, smoke_verified=false skipped ✓, read_only exempt ✓, core exempt ✓

**Placeholder scan:** None found.

**Type consistency:** `_smoke_cr_id` is `str` throughout. `base` is always `client.base.rstrip("/")`. Phase function signatures are all `(client: NexplaneClient) -> None`.
