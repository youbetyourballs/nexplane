#!/usr/bin/env python3
"""Check and fix Defender for Endpoint permissions."""
import asyncio
import httpx
import json
import sys
import os

sys.path.insert(0, "/app")
os.chdir("/app")

from app.services.secret_backend_factory import get_secret_backend
from app.database import engine
from sqlalchemy import text


async def main():
    async with engine.begin() as conn:
        row = await conn.execute(text(
            "SELECT cc.credentials_encrypted FROM connectors c "
            "JOIN connector_credentials cc ON cc.connector_id=c.id "
            "WHERE c.connector_type::text='azure' LIMIT 1"
        ))
        enc = row.scalar_one_or_none()
    creds = get_secret_backend().decrypt_json(enc)
    tenant = creds["tenant_id"]
    client_id = creds["client_id"]
    client_secret = creds["client_secret"]

    # Get Graph token
    r = httpx.post(
        f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        data={"grant_type": "client_credentials", "client_id": client_id,
              "client_secret": client_secret, "scope": "https://graph.microsoft.com/.default"},
    )
    r.raise_for_status()
    graph_token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {graph_token}", "Content-Type": "application/json"}

    # Get our SP
    our_sp = httpx.get(
        f"https://graph.microsoft.com/v1.0/servicePrincipals?$filter=appId eq '{client_id}'",
        headers=headers,
    ).json()["value"][0]
    our_sp_id = our_sp["id"]
    print(f"Our SP: {our_sp_id}")

    # Check existing app role assignments
    assignments = httpx.get(
        f"https://graph.microsoft.com/v1.0/servicePrincipals/{our_sp_id}/appRoleAssignments",
        headers=headers,
    ).json()
    print(f"Current app role assignments ({len(assignments.get('value', []))}):")
    for a in assignments.get("value", []):
        print(f"  resourceDisplayName={a.get('resourceDisplayName')} roleId={a.get('appRoleId')}")

    # Get Defender SP
    wdatp_r = httpx.get(
        "https://graph.microsoft.com/v1.0/servicePrincipals?$filter=displayName eq 'WindowsDefenderATP'",
        headers=headers,
    ).json()
    wdatp_sps = wdatp_r.get("value", [])
    if not wdatp_sps:
        print("WindowsDefenderATP SP not found")
        return
    wdatp_sp_id = wdatp_sps[0]["id"]
    print(f"\nWindowsDefenderATP SP id: {wdatp_sp_id}")

    # Get Defender SP detail with appRoles
    wdatp_detail = httpx.get(
        f"https://graph.microsoft.com/v1.0/servicePrincipals/{wdatp_sp_id}?$select=appRoles",
        headers=headers,
    ).json()
    all_roles = wdatp_detail.get("appRoles", [])
    print(f"Defender app roles available: {[r['value'] for r in all_roles]}")

    target_roles = {r["value"]: r["id"] for r in all_roles
                    if r["value"] in ("Machine.Read.All", "Machine.Isolate", "Alert.Read.All")}
    print(f"Target roles: {target_roles}")

    # Check which are already assigned to our SP
    assigned_ids = {a["appRoleId"] for a in assignments.get("value", []) if a.get("resourceId") == wdatp_sp_id}
    print(f"Already assigned IDs: {assigned_ids}")

    # Grant missing ones
    for role_name, role_id in target_roles.items():
        if role_id in assigned_ids:
            print(f"  {role_name}: already assigned")
            continue
        grant_r = httpx.post(
            f"https://graph.microsoft.com/v1.0/servicePrincipals/{our_sp_id}/appRoleAssignments",
            headers=headers,
            json={
                "principalId": our_sp_id,
                "resourceId": wdatp_sp_id,
                "appRoleId": role_id,
            },
        )
        if grant_r.status_code in (200, 201):
            print(f"  {role_name}: granted")
        elif grant_r.status_code == 409:
            print(f"  {role_name}: already exists")
        else:
            print(f"  {role_name}: FAILED {grant_r.status_code} {grant_r.text[:200]}")

    # Try the Defender API directly to test
    print("\nTesting Defender API with client_credentials token...")
    defender_r = httpx.post(
        f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        data={"grant_type": "client_credentials", "client_id": client_id,
              "client_secret": client_secret,
              "scope": "https://api.securitycenter.microsoft.com/.default"},
    )
    if defender_r.status_code != 200:
        print(f"Failed to get Defender token: {defender_r.status_code} {defender_r.text[:200]}")
        return
    defender_token = defender_r.json()["access_token"]
    print("Got Defender token OK")

    test_r = httpx.get(
        "https://api.securitycenter.microsoft.com/api/machines",
        headers={"Authorization": f"Bearer {defender_token}"},
        timeout=30,
    )
    print(f"GET /api/machines: {test_r.status_code}")
    if test_r.status_code == 200:
        machines = test_r.json().get("value", [])
        print(f"  Machines count: {len(machines)}")
        for m in machines[:3]:
            print(f"    - {m.get('computerDnsName')} ({m.get('id', '')[:8]}...)")
    else:
        print(f"  Error: {test_r.text[:500]}")


if __name__ == "__main__":
    asyncio.run(main())
