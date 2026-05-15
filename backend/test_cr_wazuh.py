"""Temporary test script for wazuh_deploy_agent CR flow. Delete after testing."""
import httpx

BASE = "http://localhost:8000"
r = httpx.post(f"{BASE}/auth/login", json={"email": "admin@acme.example", "password": "admin123"}, timeout=10)
token = r.json()["access_token"]
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

cr = httpx.post(f"{BASE}/change-requests", headers=headers, json={
    "title": "test wazuh deploy agent",
    "description": "smoke test",
    "change_type": "wazuh_deploy_agent",
    "target_asset_ids": ["00000000-0000-0000-0001-000000000003"],
    "desired_outcome": {"agent_name": "test-agent-001", "rollback_strategy": "snapshot_restore", "_smoke_test": True}
}, timeout=15)
print(f"CR create: {cr.status_code}")
if cr.status_code >= 400:
    print(cr.text[:400])
    exit(1)
cr_id = cr.json()["id"]
print(f"CR ID: {cr_id}")

plan = httpx.post(f"{BASE}/change-requests/{cr_id}/plan", headers=headers, timeout=60)
print(f"Plan: {plan.status_code}")
if plan.status_code >= 400:
    print(plan.text[:400])
    exit(1)
print(f"Plan steps: {[s.get('connector_type') for s in plan.json().get('generated_steps', [])]}")

subm = httpx.post(f"{BASE}/change-requests/{cr_id}/submit-for-approval", headers=headers, timeout=15)
print(f"Submit: {subm.status_code}")
if subm.status_code >= 400:
    print(subm.text[:400])
    exit(1)

appr = httpx.post(f"{BASE}/change-requests/{cr_id}/approve", headers=headers,
                  json={"decision": "approved", "comment": "test"}, timeout=15)
print(f"Approve: {appr.status_code}")
if appr.status_code >= 400:
    print(appr.text[:400])
    exit(1)

print("CR flow PASSED (plan + submit + approve)")
