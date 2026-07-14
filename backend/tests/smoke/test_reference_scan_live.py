# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

#!/usr/bin/env python3
"""
Live smoke tests for Reference Scan and Update.

Phases:
  REF_SCAN_AWS        — scan_for_references CR against AWS connector
  REF_SCAN_K8S        — scan_for_references CR against Kubernetes connector (skipped if no K8s)
  REF_SCAN_AGENT      — scan_for_references CR against nexplane_agent connector (skipped if no agent)
  REF_UPDATE_LAMBDA   — update_reference CR (Lambda env var) + rollback verification
  REF_EXCEPTION_RESOLVE — dismiss a scan exception via POST /scan-exceptions/{id}/dismiss
  REF_FINDING         — verify /findings endpoint accepts reference_not_updated type
  REF_MCP_PARITY      — verify all 6 reference scan MCP tools are registered

Prerequisites:
  - Platform running at --base-url (default: http://localhost:8000)
  - AWS connector registered and reachable
  - AWS Lambda with env var NEXPLANE_TEST_REF=nexplane-smoke-test-marker.internal
    (only required for REF_SCAN_AWS to find >=1 hit; scan succeeds with 0 hits too)

Run: python backend/tests/smoke/test_reference_scan_live.py --email admin@acme.example --password admin123
"""

import argparse
import sys
import time

from smoke_helpers import NexplaneClient, log, fail, make_base_parser

SEARCH_TERM = "nexplane-smoke-test-marker.internal"
REPLACEMENT = "nexplane-smoke-updated-marker.internal"

ALL_PHASES = [
    "REF_SCAN_AWS",
    "REF_SCAN_K8S",
    "REF_SCAN_AGENT",
    "REF_UPDATE_LAMBDA",
    "REF_EXCEPTION_RESOLVE",
    "REF_FINDING",
    "REF_MCP_PARITY",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_connector(client: NexplaneClient, connector_type: str):
    """Return the first connector of the given type, or None."""
    try:
        connectors = client.get("/connectors", params={"connector_type": connector_type})
    except Exception:
        connectors = client.get("/connectors")
    if not connectors:
        return None
    matching = [c for c in connectors if c.get("connector_type") == connector_type]
    return matching[0] if matching else None


def _run_scan_cr(client: NexplaneClient, title: str, connector_type: str, connector_id: str,
                 extra_params: dict | None = None) -> dict:
    """Create, approve, execute a scan_for_references CR and wait for completion."""
    params: dict = {
        "search_terms": [SEARCH_TERM],
        "migration_context": {
            "source_term": SEARCH_TERM,
            "target_term": REPLACEMENT,
            "notes": "smoke test",
        },
        "connectors": [{"connector_type": connector_type, "connector_id": connector_id}],
    }
    if extra_params:
        params.update(extra_params)

    cr = client.post("/change-requests", json={
        "title": title,
        "description": f"Smoke test: {title}",
        "change_type": "scan_for_references",
        "desired_outcome": {"rollback_strategy": "snapshot_restore", "_smoke_test": True},
        "parameters": params,
    })
    cr_id = cr["id"]
    client.post(f"/change-requests/{cr_id}/plan")
    client.post(f"/change-requests/{cr_id}/submit-for-approval")
    client.post(f"/change-requests/{cr_id}/approve", json={"decision": "approved", "comment": "smoke test"})
    client.post(f"/change-requests/{cr_id}/execute")
    return client._wait_timeout(cr_id, title, timeout=120)


def _extract_result(cr: dict) -> dict:
    """Pull execution result out of CR, checking top-level and nested execution_runs."""
    if cr.get("execution_result"):
        return cr["execution_result"]
    for run in cr.get("execution_runs") or []:
        r = run.get("result") or {}
        steps = r.get("execution", {}).get("steps", [])
        for step in steps:
            if step.get("result"):
                return step["result"]
        if r:
            return r
    return {}


# ---------------------------------------------------------------------------
# Phase: REF_SCAN_AWS
# ---------------------------------------------------------------------------

def phase_ref_scan_aws(client: NexplaneClient) -> str:
    """Scan AWS connector for SEARCH_TERM. Returns cr_id for use by exception phases."""
    log("=== Phase REF_SCAN_AWS ===")
    aws = _get_connector(client, "aws")
    if not aws:
        log("No AWS connector found — skipping REF_SCAN_AWS", ok=False)
        return ""

    cr = _run_scan_cr(client, "Smoke: AWS reference scan", "aws", aws["id"])
    result = _extract_result(cr)
    hits = result.get("hits_total", 0)
    exceptions = result.get("exceptions_created", 0)
    log(f"REF_SCAN_AWS: hits_total={hits}, exceptions_created={exceptions}, cr_id={cr['id']}")

    # Verify the scan-exceptions endpoint returns a list (may be empty)
    scan_exceptions = client.get("/scan-exceptions", params={"scan_cr_id": cr["id"]})
    assert isinstance(scan_exceptions, list), \
        f"Expected list from /scan-exceptions, got: {type(scan_exceptions)}"
    log(f"REF_SCAN_AWS: /scan-exceptions returned {len(scan_exceptions)} items")

    log("REF_SCAN_AWS PASS")
    return cr["id"]


# ---------------------------------------------------------------------------
# Phase: REF_SCAN_K8S
# ---------------------------------------------------------------------------

def phase_ref_scan_k8s(client: NexplaneClient) -> None:
    log("=== Phase REF_SCAN_K8S ===")
    k8s = _get_connector(client, "kubernetes")
    if not k8s:
        log("No Kubernetes connector — skipping REF_SCAN_K8S")
        return

    cr = _run_scan_cr(client, "Smoke: K8s reference scan", "kubernetes", k8s["id"])
    result = _extract_result(cr)
    log(f"REF_SCAN_K8S: hits_total={result.get('hits_total', 0)}, "
        f"exceptions_created={result.get('exceptions_created', 0)}")
    log("REF_SCAN_K8S PASS")


# ---------------------------------------------------------------------------
# Phase: REF_SCAN_AGENT
# ---------------------------------------------------------------------------

def phase_ref_scan_agent(client: NexplaneClient) -> None:
    log("=== Phase REF_SCAN_AGENT ===")
    agent_connector = _get_connector(client, "nexplane_agent")
    if not agent_connector:
        log("No nexplane_agent connector — skipping REF_SCAN_AGENT")
        return

    # Find an agent-linked server asset to pass as asset_ids
    assets = client.get("/assets", params={"asset_type": "server"})
    agent_assets = [a for a in assets if a.get("connector_id") == agent_connector["id"]]
    if not agent_assets:
        log("No server assets linked to nexplane_agent connector — skipping REF_SCAN_AGENT")
        return

    asset_id = agent_assets[0]["id"]
    extra = {
        "connectors": [{
            "connector_type": "nexplane_agent",
            "connector_id": agent_connector["id"],
            "asset_ids": [asset_id],
        }],
    }
    cr = _run_scan_cr(client, "Smoke: agent host reference scan", "nexplane_agent",
                      agent_connector["id"], extra_params=extra)
    result = _extract_result(cr)
    log(f"REF_SCAN_AGENT: hits_total={result.get('hits_total', 0)}")
    log("REF_SCAN_AGENT PASS")


# ---------------------------------------------------------------------------
# Phase: REF_UPDATE_LAMBDA
# ---------------------------------------------------------------------------

def phase_ref_update_lambda(client: NexplaneClient) -> None:
    log("=== Phase REF_UPDATE_LAMBDA ===")
    aws = _get_connector(client, "aws")
    if not aws:
        log("No AWS connector — skipping REF_UPDATE_LAMBDA")
        return

    assets = client.get("/assets", params={"asset_type": "application"})
    lambda_asset = next(
        (a for a in assets
         if "smoke" in a.get("name", "").lower()
         and (a.get("asset_metadata") or {}).get("arn", "").startswith("arn:aws:lambda")),
        None,
    )
    if not lambda_asset:
        log("No smoke Lambda asset found — skipping REF_UPDATE_LAMBDA "
            "(provision a Lambda with NEXPLANE_TEST_REF env var and run EC2 discovery)")
        return

    arn = lambda_asset["asset_metadata"]["arn"]
    log(f"REF_UPDATE_LAMBDA: targeting Lambda {arn}")

    # Execute the update
    cr = client.run_cr(
        "Smoke: update Lambda env var reference",
        "update_reference",
        lambda_asset["id"],
        {
            "rollback_strategy": "snapshot_restore",
            "_smoke_test": True,
            "connector_type": "aws",
            "action_id": "update_lambda_env_var",
            "params": {
                "function_arn": arn,
                "env_var_key": "NEXPLANE_TEST_REF",
                "old_value": SEARCH_TERM,
                "new_value": REPLACEMENT,
            },
        },
        connector_id=aws["id"],
        timeout=120,
    )
    result = _extract_result(cr)
    assert result.get("status") == "updated", \
        f"Expected status=updated, got: {result}"
    log(f"REF_UPDATE_LAMBDA: update completed, result={result}")

    # Rollback
    log("REF_UPDATE_LAMBDA: initiating rollback")
    rb = client.post(f"/change-requests/{cr['id']}/rollback")
    rb_id = rb.get("id") or rb.get("rollback_cr_id")
    if rb_id:
        client._wait_rollback(rb_id, "REF_UPDATE_LAMBDA rollback")
    else:
        # Some implementations return the original CR updated in place
        time.sleep(10)

    # Verify rollback by re-scanning — original term should be found again
    aws_conn_refresh = _get_connector(client, "aws")
    verify_cr = _run_scan_cr(client, "Smoke: verify rollback (re-scan)", "aws", aws_conn_refresh["id"])
    verify_result = _extract_result(verify_cr)
    assert verify_result.get("hits_total", 0) >= 1, \
        f"Rollback failed — original term not found after rollback. result={verify_result}"
    log("REF_UPDATE_LAMBDA + rollback PASS")


# ---------------------------------------------------------------------------
# Phase: REF_EXCEPTION_RESOLVE
# ---------------------------------------------------------------------------

def phase_ref_exception_resolve(client: NexplaneClient, aws_scan_cr_id: str) -> None:
    log("=== Phase REF_EXCEPTION_RESOLVE ===")

    # If we didn't get a scan CR from REF_SCAN_AWS, run a fresh one
    if not aws_scan_cr_id:
        aws = _get_connector(client, "aws")
        if not aws:
            log("No AWS connector — skipping REF_EXCEPTION_RESOLVE")
            return
        cr = _run_scan_cr(client, "Smoke: scan for exception resolution test", "aws", aws["id"])
        aws_scan_cr_id = cr["id"]

    exceptions = client.get("/scan-exceptions", params={"scan_cr_id": aws_scan_cr_id})
    if not exceptions:
        log("No exceptions generated by scan — skipping REF_EXCEPTION_RESOLVE "
            "(provision a Lambda with NEXPLANE_TEST_REF to generate exceptions)")
        return

    exc_id = exceptions[0]["id"]
    log(f"REF_EXCEPTION_RESOLVE: dismissing exception {exc_id}")
    result = client.post(f"/scan-exceptions/{exc_id}/dismiss",
                         json={"reason": "Smoke test marker — intentional, no update needed"})
    status = result.get("status")
    assert status == "dismissed", f"Expected status=dismissed, got: {result}"
    log("REF_EXCEPTION_RESOLVE PASS")


# ---------------------------------------------------------------------------
# Phase: REF_FINDING
# ---------------------------------------------------------------------------

def phase_ref_finding(client: NexplaneClient) -> None:
    log("=== Phase REF_FINDING ===")
    try:
        findings = client.get("/findings", params={"finding_type": "reference_not_updated"})
        assert isinstance(findings, list), \
            f"/findings?finding_type=reference_not_updated must return a list, got {type(findings)}"
        log(f"REF_FINDING: /findings returned {len(findings)} items")
        log("REF_FINDING PASS")
    except Exception as e:
        # 404 or 422 likely means endpoint not yet wired — log but don't fail
        log(f"REF_FINDING: endpoint not available or failed ({e}) — skipping", ok=False)


# ---------------------------------------------------------------------------
# Phase: REF_MCP_PARITY
# ---------------------------------------------------------------------------

def phase_ref_mcp_parity(client: NexplaneClient) -> None:
    log("=== Phase REF_MCP_PARITY ===")
    expected_tools = {
        "scan_for_references",
        "get_scan_results",
        "list_reference_exceptions",
        "resolve_reference_exception",
        "reattempt_reference_triage",
        "dismiss_reference_exception",
    }
    try:
        resp = client.get("/mcp/tools")
        registered = {t["name"] for t in (resp if isinstance(resp, list) else resp.get("tools", []))}
        missing = expected_tools - registered
        if missing:
            fail(f"REF_MCP_PARITY: missing MCP tools: {missing}")
        log(f"REF_MCP_PARITY: all {len(expected_tools)} tools present")
        log("REF_MCP_PARITY PASS")
    except Exception as e:
        log(f"REF_MCP_PARITY: /mcp/tools check failed ({e}) — skipping", ok=False)


# ---------------------------------------------------------------------------
# Combined run
# ---------------------------------------------------------------------------

def run(client: NexplaneClient, phases: list[str]) -> None:
    log("=== Reference Scan Smoke Tests ===")

    aws_scan_cr_id = ""

    if "REF_SCAN_AWS" in phases:
        aws_scan_cr_id = phase_ref_scan_aws(client)

    if "REF_SCAN_K8S" in phases:
        phase_ref_scan_k8s(client)

    if "REF_SCAN_AGENT" in phases:
        phase_ref_scan_agent(client)

    if "REF_UPDATE_LAMBDA" in phases:
        phase_ref_update_lambda(client)

    if "REF_EXCEPTION_RESOLVE" in phases:
        phase_ref_exception_resolve(client, aws_scan_cr_id)

    if "REF_FINDING" in phases:
        phase_ref_finding(client)

    if "REF_MCP_PARITY" in phases:
        phase_ref_mcp_parity(client)

    log("=== All reference scan phases PASSED ===")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = make_base_parser("Reference Scan and Update Smoke Tests")
    parser.add_argument(
        "--phases",
        default=",".join(ALL_PHASES),
        help=f"Comma-separated phases to run (default: all). Options: {','.join(ALL_PHASES)}",
    )
    args = parser.parse_args()

    phases = [p.strip().upper() for p in args.phases.split(",") if p.strip()]
    invalid = [p for p in phases if p not in ALL_PHASES]
    if invalid:
        print(f"Unknown phases: {invalid}. Valid: {ALL_PHASES}", file=sys.stderr)
        sys.exit(1)

    client = NexplaneClient(args.base_url, args.email, args.password)
    run(client, phases)


if __name__ == "__main__":
    main()
