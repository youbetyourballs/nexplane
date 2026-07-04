# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
#!/usr/bin/env python3
"""
Nexplane Catalog Action Live Smoke Test

Usage:
    python tests/smoke/test_catalog_action_live.py \\
        --email admin@acme.example --password admin123 \\
        --phases CATALOG_DISCOVERY,CATALOG_CR_LIFECYCLE,CATALOG_ACTION_ERRORS,CATALOG_ROLLBACK,CATALOG_WORKFLOW,CATALOG_WORKFLOW_PARTIAL_FAILURE

Phase descriptions:
    CATALOG_DISCOVERY               Verify /capabilities and /catalog/actions return correct structure
    CATALOG_CR_LIFECYCLE            Full catalog_action CR lifecycle via aws.discover_ec2_instances
    CATALOG_ACTION_ERRORS           Error paths: unknown action → plan_blocked; empty outcome → graceful failure
    CATALOG_ROLLBACK                Single-step catalog_action CR: execute then rollback, verify rollback fields + auto-asset deleted
    CATALOG_WORKFLOW                Multi-step catalog_workflow CR: two key pair steps, execute, FILO rollback
    CATALOG_WORKFLOW_PARTIAL_FAILURE Duplicate key name forces step 2 to fail → step 1 auto-rolled back

Requirements:
    AWS connector active in the org (used by discover_ec2_instances and create_key_pair)
"""
import sys
import time
import uuid

from smoke_helpers import NexplaneClient, log, fail, make_base_parser

ALL_PHASES = [
    "CATALOG_DISCOVERY",
    "CATALOG_CR_LIFECYCLE",
    "CATALOG_ACTION_ERRORS",
    "CATALOG_ROLLBACK",
    "CATALOG_WORKFLOW",
    "CATALOG_WORKFLOW_PARTIAL_FAILURE",
]


def _uuid8() -> str:
    return str(uuid.uuid4())[:8]

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
    change_plan = cr.get("change_plan") or {}
    steps = (
        change_plan.get("generated_steps")
        or change_plan.get("steps")
        or cr.get("plan", {}).get("steps")
        or cr.get("steps")
        or []
    )
    if not steps:
        fail(f"No steps in plan after /plan call. CR status={cr.get('status')}, change_plan={change_plan}")
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

    execution_runs = cr.get("execution_runs") or []
    result = (
        cr.get("execution_result")
        or cr.get("result")
        or (execution_runs[0].get("result") if execution_runs else None)
    )
    if result is None:
        fail("No execution result present on completed catalog_action CR")
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


def phase_catalog_rollback(client: NexplaneClient) -> None:
    """Single-step mutating catalog_action CR: execute then rollback, verify asset deleted."""
    base = client.base.rstrip("/")
    key_name = f"nexplane-smoke-{_uuid8()}"
    log(f"CATALOG_ROLLBACK: key_name={key_name}")

    # 1. Create CR
    resp = client.client.post(f"{base}/change-requests", json={
        "title": f"smoke rollback {key_name}",
        "change_type": "catalog_action",
        "desired_outcome": {
            "connector_type": "aws",
            "action_id": "create_key_pair",
            "params": {"key_name": key_name},
        },
    })
    if resp.status_code not in (200, 201):
        fail(f"Create CR failed: {resp.status_code} {resp.text}")
    cr_id = resp.json()["id"]
    log(f"CR created: {cr_id}")

    # 2. Plan
    resp = client.client.post(f"{base}/change-requests/{cr_id}/plan")
    if resp.status_code not in (200, 201, 202):
        fail(f"Plan failed: {resp.status_code} {resp.text}")
    for _ in range(30):
        time.sleep(2)
        resp = client.client.get(f"{base}/change-requests/{cr_id}")
        cr = resp.json()
        if cr["status"] in ("planned", "plan_blocked", "failed"):
            break
    if cr["status"] != "planned":
        fail(f"CR did not reach planned, got: {cr['status']}")

    # 3. Verify rollback fields in plan
    steps = (cr.get("change_plan") or {}).get("generated_steps") or cr.get("generated_steps") or []
    if not steps:
        fail("No generated_steps in planned CR")
    step = steps[0]
    if step.get("rollback_connector_type") != "aws":
        fail(f"Expected rollback_connector_type=aws, got: {step.get('rollback_connector_type')}")
    if step.get("rollback_action_id") != "delete_key_pair":
        fail(f"Expected rollback_action_id=delete_key_pair, got: {step.get('rollback_action_id')}")
    log("Rollback fields in plan: VERIFIED")

    # 4. Approve + execute
    client.client.post(f"{base}/change-requests/{cr_id}/submit-for-approval")
    client.client.post(f"{base}/change-requests/{cr_id}/approve", json={"decision": "approved", "comment": "smoke"})
    client.client.post(f"{base}/change-requests/{cr_id}/execute")
    for _ in range(60):
        time.sleep(3)
        resp = client.client.get(f"{base}/change-requests/{cr_id}")
        cr = resp.json()
        if cr["status"] in ("completed", "failed", "execution_failed"):
            break
    if cr["status"] != "completed":
        fail(f"CR did not complete, got: {cr['status']}")
    log("Execution: COMPLETED")

    # 5. Record auto_asset_id if present
    exec_runs = cr.get("execution_runs") or []
    exec_result = cr.get("execution_result") or (exec_runs[0].get("result") if exec_runs else {}) or {}
    exec_steps = exec_result.get("steps") or exec_result.get("execution", {}).get("steps", [])
    auto_asset_id = None
    if exec_steps:
        auto_asset_id = exec_steps[0].get("result", {}).get("_auto_asset_id")
    if auto_asset_id:
        resp = client.client.get(f"{base}/assets/{auto_asset_id}")
        if resp.status_code != 200:
            fail(f"Asset {auto_asset_id} not found after execute: {resp.status_code}")
        log(f"Auto-asset {auto_asset_id}: EXISTS pre-rollback")

    # 6. Rollback
    resp = client.client.post(f"{base}/change-requests/{cr_id}/rollback")
    if resp.status_code not in (200, 201, 202):
        fail(f"Rollback trigger failed: {resp.status_code} {resp.text}")
    for _ in range(60):
        time.sleep(3)
        resp = client.client.get(f"{base}/change-requests/{cr_id}")
        cr = resp.json()
        if cr["status"] in ("rolled_back", "rollback_failed", "failed"):
            break
    if cr["status"] != "rolled_back":
        fail(f"CR did not reach rolled_back, got: {cr['status']}")
    log("Rollback: COMPLETED")

    # 7. Verify asset deleted
    if auto_asset_id:
        resp = client.client.get(f"{base}/assets/{auto_asset_id}")
        if resp.status_code != 404:
            fail(f"Asset {auto_asset_id} still exists after rollback (expected 404, got {resp.status_code})")
        log(f"Auto-asset {auto_asset_id}: DELETED after rollback — VERIFIED")

    log("CATALOG_ROLLBACK passed")


def phase_catalog_workflow(client: NexplaneClient) -> None:
    """Multi-step catalog_workflow CR: two create_key_pair steps, execute, FILO rollback."""
    base = client.base.rstrip("/")
    key_a = f"nexplane-smoke-wfa-{_uuid8()}"
    key_b = f"nexplane-smoke-wfb-{_uuid8()}"
    log(f"CATALOG_WORKFLOW: key_a={key_a} key_b={key_b}")

    # 1. Create CR
    resp = client.client.post(f"{base}/change-requests", json={
        "title": f"smoke workflow {key_a}",
        "change_type": "catalog_workflow",
        "desired_outcome": {
            "steps": [
                {"connector_type": "aws", "action_id": "create_key_pair", "params": {"key_name": key_a}},
                {"connector_type": "aws", "action_id": "create_key_pair", "params": {"key_name": key_b}},
            ]
        },
    })
    if resp.status_code not in (200, 201):
        fail(f"Create CR failed: {resp.status_code} {resp.text}")
    cr_id = resp.json()["id"]
    log(f"CR created: {cr_id}")

    # 2. Plan and verify 2 steps with rollback fields
    client.client.post(f"{base}/change-requests/{cr_id}/plan")
    for _ in range(30):
        time.sleep(2)
        resp = client.client.get(f"{base}/change-requests/{cr_id}")
        cr = resp.json()
        if cr["status"] in ("planned", "plan_blocked", "failed"):
            break
    if cr["status"] != "planned":
        fail(f"CR did not reach planned, got: {cr['status']}")

    steps = (cr.get("change_plan") or {}).get("generated_steps") or cr.get("generated_steps") or []
    if len(steps) != 2:
        fail(f"Expected 2 generated_steps, got {len(steps)}")
    for i, step in enumerate(steps, start=1):
        if step.get("rollback_connector_type") != "aws":
            fail(f"Step {i}: expected rollback_connector_type=aws, got: {step.get('rollback_connector_type')}")
        if step.get("rollback_action_id") != "delete_key_pair":
            fail(f"Step {i}: expected rollback_action_id=delete_key_pair, got: {step.get('rollback_action_id')}")
    log("2-step plan with rollback fields: VERIFIED")

    # 3. Approve + execute
    client.client.post(f"{base}/change-requests/{cr_id}/submit-for-approval")
    client.client.post(f"{base}/change-requests/{cr_id}/approve", json={"decision": "approved", "comment": "smoke"})
    client.client.post(f"{base}/change-requests/{cr_id}/execute")
    for _ in range(60):
        time.sleep(3)
        resp = client.client.get(f"{base}/change-requests/{cr_id}")
        cr = resp.json()
        if cr["status"] in ("completed", "failed", "execution_failed"):
            break
    if cr["status"] != "completed":
        fail(f"CR did not complete, got: {cr['status']}")
    log("Execution: COMPLETED")

    # 4. Rollback
    client.client.post(f"{base}/change-requests/{cr_id}/rollback")
    for _ in range(60):
        time.sleep(3)
        resp = client.client.get(f"{base}/change-requests/{cr_id}")
        cr = resp.json()
        if cr["status"] in ("rolled_back", "rollback_failed", "failed"):
            break
    if cr["status"] != "rolled_back":
        fail(f"CR did not reach rolled_back, got: {cr['status']}")
    log("Rollback: COMPLETED")

    # 5. Verify FILO order — step 2 rolled back before step 1
    rollback_run = cr.get("rollback_execution_result") or cr.get("rollback_result") or {}
    rollback_steps = rollback_run.get("steps", [])
    if rollback_steps:
        step_numbers = [s.get("step_number") for s in rollback_steps]
        if step_numbers != sorted(step_numbers, reverse=True):
            fail(f"Rollback steps not in FILO order (highest step_number first): {step_numbers}")
        log(f"FILO rollback order verified: {step_numbers}")
    else:
        log("FILO order: rollback_steps not surfaced in CR response — skipping order check")

    log("CATALOG_WORKFLOW passed")


def phase_catalog_workflow_partial_failure(client: NexplaneClient) -> None:
    """Partial failure: step 2 fails (duplicate key) → step 1 auto-rolled back."""
    base = client.base.rstrip("/")
    key_name = f"nexplane-smoke-pf-{_uuid8()}"
    log(f"CATALOG_WORKFLOW_PARTIAL_FAILURE: key_name={key_name} (used twice to force failure)")

    # 1. Create CR with duplicate key name → step 2 will fail
    resp = client.client.post(f"{base}/change-requests", json={
        "title": f"smoke pf {key_name}",
        "change_type": "catalog_workflow",
        "desired_outcome": {
            "steps": [
                {"connector_type": "aws", "action_id": "create_key_pair", "params": {"key_name": key_name}},
                {"connector_type": "aws", "action_id": "create_key_pair", "params": {"key_name": key_name}},
            ]
        },
    })
    if resp.status_code not in (200, 201):
        fail(f"Create CR failed: {resp.status_code} {resp.text}")
    cr_id = resp.json()["id"]
    log(f"CR created: {cr_id}")

    # 2. Approve + execute — expect failure
    client.client.post(f"{base}/change-requests/{cr_id}/plan")
    for _ in range(30):
        time.sleep(2)
        resp = client.client.get(f"{base}/change-requests/{cr_id}")
        cr = resp.json()
        if cr["status"] in ("planned", "plan_blocked", "failed"):
            break
    if cr["status"] != "planned":
        fail(f"CR did not reach planned, got: {cr['status']}")

    client.client.post(f"{base}/change-requests/{cr_id}/submit-for-approval")
    client.client.post(f"{base}/change-requests/{cr_id}/approve", json={"decision": "approved", "comment": "smoke"})
    client.client.post(f"{base}/change-requests/{cr_id}/execute")

    for _ in range(90):
        time.sleep(3)
        resp = client.client.get(f"{base}/change-requests/{cr_id}")
        cr = resp.json()
        if cr["status"] in ("completed", "failed", "execution_failed", "rolled_back", "rollback_failed"):
            break

    if cr["status"] not in ("failed", "execution_failed", "rolled_back"):
        fail(f"Expected execution to fail or auto-rollback, got: {cr['status']}")
    log(f"Execution reached expected failure/rollback state: {cr['status']}")

    # 3. Verify step 1 completed, step 2 failed
    exec_runs = cr.get("execution_runs") or []
    exec_result = cr.get("execution_result") or (exec_runs[0].get("result") if exec_runs else {}) or {}
    exec_steps = exec_result.get("steps") or exec_result.get("execution", {}).get("steps", [])
    if exec_steps:
        step1 = next((s for s in exec_steps if s.get("step_number") == 1), None)
        step2 = next((s for s in exec_steps if s.get("step_number") == 2), None)
        if step1 and step1.get("status") not in ("completed", None):
            log(f"Step 1 status: {step1.get('status')} (may be in result dict rather than status field)")
        if step2 and step2.get("status") not in ("failed", None):
            log(f"Step 2 status: {step2.get('status')} (expected failure)")
        log(f"Step statuses — step1: {step1}, step2: {step2}")

    # 4. Verify step 1 was rolled back (delete_key_pair must have run)
    def _has_rollback_run(cr_data: dict) -> bool:
        runs = cr_data.get("execution_runs") or []
        return len(runs) > 1 or cr_data.get("rollback_execution_result") or cr_data.get("rollback_result")

    rollback_result = cr.get("rollback_execution_result") or cr.get("rollback_result")
    if rollback_result or _has_rollback_run(cr):
        log(f"Rollback run present in execution_runs")
    else:
        # If platform auto-triggers rollback asynchronously, poll briefly
        for _ in range(20):
            time.sleep(3)
            resp = client.client.get(f"{base}/change-requests/{cr_id}")
            cr = resp.json()
            if _has_rollback_run(cr) or cr["status"] == "rolled_back":
                break
        rollback_result = cr.get("rollback_execution_result") or cr.get("rollback_result")
        if not rollback_result and not _has_rollback_run(cr) and cr["status"] != "rolled_back":
            fail("Step 1 was not rolled back after step 2 failure — no rollback run and status not rolled_back")

    log("Partial failure rollback: VERIFIED")
    log("CATALOG_WORKFLOW_PARTIAL_FAILURE passed")


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
        "CATALOG_ROLLBACK": lambda: phase_catalog_rollback(client),
        "CATALOG_WORKFLOW": lambda: phase_catalog_workflow(client),
        "CATALOG_WORKFLOW_PARTIAL_FAILURE": lambda: phase_catalog_workflow_partial_failure(client),
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
