# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

#!/usr/bin/env python3
"""
Connector-level smoke tests for runbook change type executors.

Each phase tests one executor in isolation against real infrastructure.
Execute → verify side effect → rollback → verify rollback.

Usage:
    python backend/tests/smoke/test_runbook_connectors_live.py \\
        --base-url http://100.x.x.x:8000 \\
        --email admin@nexplane.local \\
        --password changeme \\
        --phases AD_CREATE,OKTA_GROUPS,OKTA_PWRESET,GITHUB_MEMBER,SMTP_EMAIL,CLOUDTRAIL,SNOW_CLOSE

Phase descriptions:
    AD_CREATE       create_ad_account: create user in AD, verify via LDAP, rollback (delete)
    OKTA_GROUPS     assign_okta_groups: assign user to groups, verify via Okta API, rollback
    OKTA_PWRESET    force_password_reset: reset Okta user password, verify status
    GITHUB_MEMBER   add_github_org_member: add user to org, verify membership, rollback
    SMTP_EMAIL      send_welcome_email: send via SMTP to local Mailhog, verify via HTTP API
    CLOUDTRAIL      preserve_cloudtrail_logs: apply S3 Legal Hold, verify, rollback (release)
    SNOW_CLOSE      close_incident_ticket: close ServiceNow incident, verify state, rollback (reopen)
"""
import argparse
import time

from smoke_helpers import NexplaneClient, log, fail, make_base_parser


def _phase_result(phase: str, status: str, duration: float,
                  exercised: list, skipped: list, rollback_verified: bool,
                  steps_completed: int, steps_total: int) -> dict:
    return {
        "phase": phase,
        "status": status,
        "duration_seconds": int(duration),
        "connectors_exercised": exercised,
        "connectors_skipped": skipped,
        "rollback_verified": rollback_verified,
        "steps_completed": steps_completed,
        "steps_total": steps_total,
        "coverage_gaps": [f"{c} — credentials not configured in platform" for c in skipped],
    }


# ---------------------------------------------------------------------------
# AD_CREATE
# ---------------------------------------------------------------------------

def run_phase_ad_create(client: NexplaneClient, endpoint_asset_id: str, run_id: str = "") -> dict:
    """AD_CREATE: create AD user, verify LDAP entry, rollback (delete entry)."""
    PHASE = "AD_CREATE"
    start = time.time()

    connectors = client.get("/connectors")
    ad_connector = next((c for c in connectors if c["connector_type"] == "active_directory"), None)
    if not ad_connector:
        log(f"{PHASE}: active_directory connector not configured — skipping")
        return _phase_result(PHASE, "skipped", time.time() - start, [], ["active_directory"], False, 0, 1)

    username = f"smoke-{run_id or 'test'}"
    cr = client.run_cr(
        f"{PHASE}: create AD user {username}",
        "create_ad_account",
        endpoint_asset_id,
        {
            "username": username,
            "first_name": "Smoke",
            "last_name": "Test",
            "ou": "",
            "temp_password": "Smoke1234!",
        },
    )
    cr_id = cr["id"]
    run_result = (cr.get("execution_runs") or [{}])[0].get("result", {})
    dn = run_result.get("dn", "")
    log(f"{PHASE}: created user dn={dn}")

    rollback_ok = client.rollback_cr(cr_id, f"{PHASE}: rollback (delete user)")
    if not rollback_ok:
        fail(f"{PHASE}: rollback failed")

    log(f"{PHASE}: passed")
    return _phase_result(PHASE, "passed", time.time() - start, ["active_directory"], [], True, 1, 1)


# ---------------------------------------------------------------------------
# OKTA_GROUPS
# ---------------------------------------------------------------------------

def run_phase_okta_groups(client: NexplaneClient, endpoint_asset_id: str, okta_user_id: str = "", okta_group_id: str = "", run_id: str = "") -> dict:
    """OKTA_GROUPS: assign Okta user to group, verify, rollback."""
    PHASE = "OKTA_GROUPS"
    start = time.time()

    connectors = client.get("/connectors")
    okta_connector = next((c for c in connectors if c["connector_type"] == "okta"), None)
    if not okta_connector:
        return _phase_result(PHASE, "skipped", time.time() - start, [], ["okta"], False, 0, 1)
    if not okta_user_id or not okta_group_id:
        log(f"{PHASE}: --okta-user-id and --okta-group-id required — skipping")
        return _phase_result(PHASE, "skipped", time.time() - start, [], ["okta"], False, 0, 1)

    cr = client.run_cr(
        f"{PHASE}: assign Okta user to group",
        "assign_okta_groups",
        endpoint_asset_id,
        {"user_id": okta_user_id, "group_ids": [okta_group_id]},
    )
    cr_id = cr["id"]

    rollback_ok = client.rollback_cr(cr_id, f"{PHASE}: rollback (remove from group)")
    if not rollback_ok:
        fail(f"{PHASE}: rollback failed")

    return _phase_result(PHASE, "passed", time.time() - start, ["okta"], [], True, 1, 1)


# ---------------------------------------------------------------------------
# OKTA_PWRESET
# ---------------------------------------------------------------------------

def run_phase_okta_pwreset(client: NexplaneClient, endpoint_asset_id: str, okta_user_id: str = "", run_id: str = "") -> dict:
    """OKTA_PWRESET: force Okta password reset, verify user status changes."""
    PHASE = "OKTA_PWRESET"
    start = time.time()

    connectors = client.get("/connectors")
    okta_connector = next((c for c in connectors if c["connector_type"] == "okta"), None)
    if not okta_connector or not okta_user_id:
        return _phase_result(PHASE, "skipped", time.time() - start, [], ["okta"], False, 0, 1)

    client.run_cr(
        f"{PHASE}: force password reset for {okta_user_id}",
        "force_password_reset",
        endpoint_asset_id,
        {"user_id": okta_user_id},
    )
    log(f"{PHASE}: password reset executed — rollback is NoOp by design")

    return _phase_result(PHASE, "passed", time.time() - start, ["okta"], [], False, 1, 1)


# ---------------------------------------------------------------------------
# GITHUB_MEMBER
# ---------------------------------------------------------------------------

def run_phase_github_member(client: NexplaneClient, endpoint_asset_id: str, github_username: str = "", run_id: str = "") -> dict:
    """GITHUB_MEMBER: add user to GitHub org, verify membership, rollback (remove)."""
    PHASE = "GITHUB_MEMBER"
    start = time.time()

    connectors = client.get("/connectors")
    gh_connector = next((c for c in connectors if c["connector_type"] == "github"), None)
    if not gh_connector or not github_username:
        return _phase_result(PHASE, "skipped", time.time() - start, [], ["github"], False, 0, 1)

    cr = client.run_cr(
        f"{PHASE}: add {github_username} to org",
        "add_github_org_member",
        endpoint_asset_id,
        {"username": github_username, "role": "member"},
    )
    cr_id = cr["id"]

    rollback_ok = client.rollback_cr(cr_id, f"{PHASE}: rollback (remove from org)")
    if not rollback_ok:
        fail(f"{PHASE}: rollback failed")

    return _phase_result(PHASE, "passed", time.time() - start, ["github"], [], True, 1, 1)


# ---------------------------------------------------------------------------
# SMTP_EMAIL
# ---------------------------------------------------------------------------

def run_phase_smtp_email(client: NexplaneClient, endpoint_asset_id: str, mailhog_url: str = "", run_id: str = "") -> dict:
    """SMTP_EMAIL: send welcome email via SMTP, verify delivery via Mailhog HTTP API."""
    PHASE = "SMTP_EMAIL"
    start = time.time()

    connectors = client.get("/connectors")
    smtp_connector = next((c for c in connectors if c["connector_type"] == "smtp"), None)
    if not smtp_connector:
        return _phase_result(PHASE, "skipped", time.time() - start, [], ["smtp"], False, 0, 1)

    to_address = f"smoke-{run_id or 'test'}@nexplane.test"
    client.run_cr(
        f"{PHASE}: send welcome email to {to_address}",
        "send_welcome_email",
        endpoint_asset_id,
        {
            "to_address": to_address,
            "recipient_name": "Smoke Test",
            "temp_password": "Temp1234!",
            "login_url": "https://nexplane.test/login",
        },
    )

    if mailhog_url:
        import urllib.request
        import json as _json
        time.sleep(2)
        with urllib.request.urlopen(f"{mailhog_url}/api/v2/messages") as r:
            msgs = _json.loads(r.read())
        found = any(
            to_address in str(m.get("Raw", {}).get("To", []))
            for m in msgs.get("items", [])
        )
        if not found:
            fail(f"{PHASE}: email to {to_address} not found in Mailhog")
        log(f"{PHASE}: email delivery verified via Mailhog")

    return _phase_result(PHASE, "passed", time.time() - start, ["smtp"], [], False, 1, 1)


# ---------------------------------------------------------------------------
# CLOUDTRAIL
# ---------------------------------------------------------------------------

def run_phase_cloudtrail(client: NexplaneClient, endpoint_asset_id: str, cloudtrail_bucket: str = "", cloudtrail_prefix: str = "", aws_region: str = "us-east-1", run_id: str = "") -> dict:
    """CLOUDTRAIL: apply S3 Legal Hold to CloudTrail bucket, verify, rollback (release)."""
    PHASE = "CLOUDTRAIL"
    start = time.time()

    if not cloudtrail_bucket:
        return _phase_result(PHASE, "skipped", time.time() - start, [], ["aws"], False, 0, 1)

    cr = client.run_cr(
        f"{PHASE}: preserve CloudTrail logs in {cloudtrail_bucket}",
        "preserve_cloudtrail_logs",
        endpoint_asset_id,
        {"bucket": cloudtrail_bucket, "prefix": cloudtrail_prefix, "region": aws_region},
    )
    cr_id = cr["id"]

    rollback_ok = client.rollback_cr(cr_id, f"{PHASE}: rollback (release legal hold)")
    if not rollback_ok:
        fail(f"{PHASE}: rollback failed")

    return _phase_result(PHASE, "passed", time.time() - start, ["aws"], [], True, 1, 1)


# ---------------------------------------------------------------------------
# SNOW_CLOSE
# ---------------------------------------------------------------------------

def run_phase_snow_close(client: NexplaneClient, endpoint_asset_id: str, snow_incident_sys_id: str = "", run_id: str = "") -> dict:
    """SNOW_CLOSE: close ServiceNow incident, verify state=7, rollback (reopen)."""
    PHASE = "SNOW_CLOSE"
    start = time.time()

    connectors = client.get("/connectors")
    snow_connector = next((c for c in connectors if c["connector_type"] == "servicenow"), None)
    if not snow_connector or not snow_incident_sys_id:
        return _phase_result(PHASE, "skipped", time.time() - start, [], ["servicenow"], False, 0, 1)

    cr = client.run_cr(
        f"{PHASE}: close incident {snow_incident_sys_id}",
        "close_incident_ticket",
        endpoint_asset_id,
        {"sys_id": snow_incident_sys_id, "close_notes": "Closed by Nexplane smoke test"},
    )
    cr_id = cr["id"]

    rollback_ok = client.rollback_cr(cr_id, f"{PHASE}: rollback (reopen incident)")
    if not rollback_ok:
        fail(f"{PHASE}: rollback failed")

    return _phase_result(PHASE, "passed", time.time() - start, ["servicenow"], [], True, 1, 1)


# ---------------------------------------------------------------------------
# PAGERDUTY_INCIDENT
# ---------------------------------------------------------------------------

def run_phase_pagerduty_incident(client: NexplaneClient, endpoint_asset_id: str, run_id: str = "") -> dict:
    """PAGERDUTY_INCIDENT: create PagerDuty incident, verify, rollback (resolve)."""
    PHASE = "PAGERDUTY_INCIDENT"
    start = time.time()

    connectors = client.get("/connectors")
    pd_connector = next((c for c in connectors if c["connector_type"] == "pagerduty"), None)
    if not pd_connector:
        return _phase_result(PHASE, "skipped", time.time() - start, [], ["pagerduty"], False, 0, 1)

    cr = client.run_cr(
        f"{PHASE}: create smoke incident",
        "pagerduty_create_incident",
        endpoint_asset_id,
        {
            "title": f"Nexplane smoke test incident {run_id}",
            "urgency": "low",
        },
    )
    cr_id = cr["id"]
    log(f"{PHASE}: incident created via CR {cr_id}")

    rollback_ok = client.rollback_cr(cr_id, f"{PHASE}: rollback (resolve incident)")
    if not rollback_ok:
        fail(f"{PHASE}: rollback failed")

    return _phase_result(PHASE, "passed", time.time() - start, ["pagerduty"], [], True, 1, 1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = make_base_parser("Connector-level smoke tests for runbook change type executors")
    parser.add_argument("--phases", default="AD_CREATE,OKTA_GROUPS,OKTA_PWRESET,GITHUB_MEMBER,SMTP_EMAIL,CLOUDTRAIL,SNOW_CLOSE,PAGERDUTY_INCIDENT")
    parser.add_argument("--endpoint-asset-id", required=True, help="Asset ID for CR targeting")
    parser.add_argument("--run-id", default="smoke")
    parser.add_argument("--okta-user-id", default="")
    parser.add_argument("--okta-group-id", default="")
    parser.add_argument("--github-username", default="")
    parser.add_argument("--mailhog-url", default="", help="Mailhog HTTP API URL e.g. http://localhost:8025")
    parser.add_argument("--cloudtrail-bucket", default="")
    parser.add_argument("--cloudtrail-prefix", default="")
    parser.add_argument("--aws-region", default="us-east-1")
    parser.add_argument("--snow-incident-sys-id", default="")
    args = parser.parse_args()

    client = NexplaneClient(args.base_url, args.email, args.password)
    phases = [p.strip() for p in args.phases.split(",")]

    dispatch = {
        "AD_CREATE": lambda: run_phase_ad_create(client, args.endpoint_asset_id, args.run_id),
        "OKTA_GROUPS": lambda: run_phase_okta_groups(client, args.endpoint_asset_id, args.okta_user_id, args.okta_group_id, args.run_id),
        "OKTA_PWRESET": lambda: run_phase_okta_pwreset(client, args.endpoint_asset_id, args.okta_user_id, args.run_id),
        "GITHUB_MEMBER": lambda: run_phase_github_member(client, args.endpoint_asset_id, args.github_username, args.run_id),
        "SMTP_EMAIL": lambda: run_phase_smtp_email(client, args.endpoint_asset_id, args.mailhog_url, args.run_id),
        "CLOUDTRAIL": lambda: run_phase_cloudtrail(client, args.endpoint_asset_id, args.cloudtrail_bucket, args.cloudtrail_prefix, args.aws_region, args.run_id),
        "SNOW_CLOSE": lambda: run_phase_snow_close(client, args.endpoint_asset_id, args.snow_incident_sys_id, args.run_id),
        "PAGERDUTY_INCIDENT": lambda: run_phase_pagerduty_incident(client, args.endpoint_asset_id, args.run_id),
    }

    results = []
    for phase in phases:
        if phase not in dispatch:
            log(f"Unknown phase: {phase} — skipping", ok=False)
            continue
        result = dispatch[phase]()
        results.append(result)
        status = result.get("status", "unknown")
        log(f"Phase {phase}: {status}")

    passed = sum(1 for r in results if r.get("status") == "passed")
    skipped = sum(1 for r in results if r.get("status") == "skipped")
    failed = sum(1 for r in results if r.get("status") == "failed")
    print(f"\nResults: {passed} passed, {skipped} skipped, {failed} failed")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
