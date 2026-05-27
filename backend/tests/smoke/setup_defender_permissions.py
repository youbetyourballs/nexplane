#!/usr/bin/env python3
"""
Setup Defender for Endpoint API permissions on the existing Azure app registration.
Uses credentials from the platform DB (azure connector).

Steps:
1. Fetch live credentials from DB
2. Find WindowsDefenderATP service principal in tenant
3. Add Machine.Read.All + Machine.Isolate app role assignments (admin consent)
4. Register a defender_endpoint connector with the same credentials
"""
import asyncio
import httpx
import json
import sys
import os

sys.path.insert(0, "/app")
os.chdir("/app")

from app.services.secret_backend_factory import get_secret_backend


async def get_live_creds():
    from app.database import engine
    from sqlalchemy import text
    async with engine.begin() as conn:
        row = await conn.execute(text(
            "SELECT cc.credentials_encrypted FROM connectors c "
            "JOIN connector_credentials cc ON cc.connector_id=c.id "
            "WHERE c.connector_type::text='azure' LIMIT 1"
        ))
        enc = row.scalar_one_or_none()
    if not enc:
        print("ERROR: No azure connector with credentials found")
        return None
    return get_secret_backend().decrypt_json(enc)


async def get_token(tenant, client_id, client_secret, scope):
    r = httpx.post(
        f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": scope,
        },
    )
    r.raise_for_status()
    return r.json()["access_token"]


async def main():
    creds = await get_live_creds()
    if not creds:
        sys.exit(1)
    tenant = creds["tenant_id"]
    client_id = creds["client_id"]
    client_secret = creds["client_secret"]
    print(f"Using tenant={tenant} client_id={client_id}")

    graph_token = await get_token(tenant, client_id, client_secret, "https://graph.microsoft.com/.default")
    headers = {"Authorization": f"Bearer {graph_token}", "Content-Type": "application/json"}

    # Find our app's service principal
    our_sp_r = httpx.get(
        f"https://graph.microsoft.com/v1.0/servicePrincipals?$filter=appId eq '{client_id}'",
        headers=headers,
    )
    our_sps = our_sp_r.json().get("value", [])
    if not our_sps:
        print("ERROR: Could not find our service principal")
        sys.exit(1)
    our_sp_id = our_sps[0]["id"]
    print(f"Our SP id: {our_sp_id}")

    # Find WindowsDefenderATP service principal
    wdatp_r = httpx.get(
        "https://graph.microsoft.com/v1.0/servicePrincipals?$filter=displayName eq 'WindowsDefenderATP'",
        headers=headers,
    )
    wdatp_sps = wdatp_r.json().get("value", [])
    if not wdatp_sps:
        # Try alternate name
        wdatp_r2 = httpx.get(
            "https://graph.microsoft.com/v1.0/servicePrincipals?$filter=displayName eq 'Microsoft Defender for Endpoint'",
            headers=headers,
        )
        wdatp_sps = wdatp_r2.json().get("value", [])
    if not wdatp_sps:
        print("ERROR: WindowsDefenderATP service principal not found in tenant.")
        print("This tenant may not have Defender for Endpoint enabled.")
        print("Try enabling it at: https://security.microsoft.com/")
        # List all SPs to help debug
        all_sp_r = httpx.get(
            "https://graph.microsoft.com/v1.0/servicePrincipals?$top=100&$select=displayName,appId",
            headers=headers,
        )
        all_sps = all_sp_r.json().get("value", [])
        security_sps = [s for s in all_sps if "defender" in s["displayName"].lower() or "security" in s["displayName"].lower() or "atp" in s["displayName"].lower()]
        print(f"Security-related SPs in tenant: {json.dumps(security_sps, indent=2)}")
        sys.exit(1)

    wdatp_sp = wdatp_sps[0]
    wdatp_sp_id = wdatp_sp["id"]
    wdatp_app_id = wdatp_sp["appId"]
    print(f"WindowsDefenderATP SP id={wdatp_sp_id} appId={wdatp_app_id}")

    # Get app roles
    all_roles = wdatp_sp.get("appRoles", [])
    if not all_roles:
        # Fetch full SP with appRoles
        sp_detail = httpx.get(
            f"https://graph.microsoft.com/v1.0/servicePrincipals/{wdatp_sp_id}",
            headers=headers,
        ).json()
        all_roles = sp_detail.get("appRoles", [])

    target_roles = {r["value"]: r["id"] for r in all_roles if r["value"] in ("Machine.Read.All", "Machine.Isolate")}
    print(f"Target roles found: {target_roles}")
    if not target_roles:
        print(f"All available roles: {json.dumps([r['value'] for r in all_roles], indent=2)}")

    # Grant app role assignments (admin consent)
    for role_name, role_id in target_roles.items():
        grant_r = httpx.post(
            f"https://graph.microsoft.com/v1.0/servicePrincipals/{wdatp_sp_id}/appRoleAssignedTo",
            headers=headers,
            json={
                "principalId": our_sp_id,
                "resourceId": wdatp_sp_id,
                "appRoleId": role_id,
            },
        )
        if grant_r.status_code in (200, 201):
            print(f"  Granted {role_name} ✓")
        elif grant_r.status_code == 409:
            print(f"  {role_name} already granted ✓")
        else:
            print(f"  Failed to grant {role_name}: {grant_r.status_code} {grant_r.text}")

    print("\nPermissions setup complete.")
    print("Now register a defender_endpoint connector via the platform API...")

    # Register defender_endpoint connector via Nexplane API
    import requests
    base = "http://localhost:8000"
    login = requests.post(f"{base}/auth/token", data={"username": "admin@acme.example", "password": "admin123"})
    token_resp = login.json()
    api_token = token_resp.get("access_token")
    if not api_token:
        print(f"Login failed: {token_resp}")
        sys.exit(1)
    api_headers = {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}

    # Check if already exists
    existing = requests.get(f"{base}/connectors", headers=api_headers).json()
    existing_defender = [c for c in existing if c.get("connector_type") == "defender_endpoint"]
    if existing_defender:
        print(f"defender_endpoint connector already exists: {existing_defender[0]['id']}")
    else:
        conn_resp = requests.post(
            f"{base}/connectors",
            headers=api_headers,
            json={
                "connector_type": "defender_endpoint",
                "name": "Microsoft Defender for Endpoint",
                "display_name": "Microsoft Defender for Endpoint",
            },
        )
        if conn_resp.status_code == 201:
            conn_id = conn_resp.json()["id"]
            print(f"Registered defender_endpoint connector: {conn_id}")
            # Store credentials
            cred_resp = requests.put(
                f"{base}/connectors/{conn_id}/credentials",
                headers=api_headers,
                json={"credentials": {
                    "tenant_id": tenant,
                    "client_id": client_id,
                    "client_secret": client_secret,
                }},
            )
            print(f"Credentials stored: {cred_resp.status_code}")
        else:
            print(f"Failed to register connector: {conn_resp.status_code} {conn_resp.text}")


if __name__ == "__main__":
    asyncio.run(main())
