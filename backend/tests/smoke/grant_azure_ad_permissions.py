"""
Grant Microsoft Graph API permissions to the Azure app registration used by Nexplane.
Permissions needed for AZURE_AD smoke: User.Read.All, User.ReadWrite.All, Group.Read.All.
Run inside the backend container: python3 /app/tests/smoke/grant_azure_ad_permissions.py
"""
import sys
sys.path.insert(0, "/app")
sys.path.insert(0, "/app/tests/smoke")

import httpx
from smoke_helpers import get_connector_creds_from_db

creds = get_connector_creds_from_db("azure_ad") or get_connector_creds_from_db("azure")
tenant_id = creds["tenant_id"]
client_id = creds["client_id"]
client_secret = creds["client_secret"]

print(f"Tenant: {tenant_id}")
print(f"Client (app): {client_id}")

# Step 1: Get access token
token_resp = httpx.post(
    f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
    data={
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "https://graph.microsoft.com/.default",
    },
)
token_resp.raise_for_status()
token = token_resp.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}
print("Token acquired.")

# Step 2: Find our app's service principal
sp_resp = httpx.get(
    f"https://graph.microsoft.com/v1.0/servicePrincipals?$filter=appId eq '{client_id}'",
    headers=headers,
)
sp_resp.raise_for_status()
sp_list = sp_resp.json().get("value", [])
if not sp_list:
    raise RuntimeError(f"No service principal found for appId {client_id}")
our_sp_id = sp_list[0]["id"]
print(f"Our SP id: {our_sp_id}")

# Step 3: Find Microsoft Graph service principal via direct appId lookup
graph_sp_resp = httpx.get(
    "https://graph.microsoft.com/v1.0/servicePrincipals(appId='00000003-0000-0000-c000-000000000000')",
    headers=headers,
)
graph_sp_resp.raise_for_status()
graph_sp = graph_sp_resp.json()
graph_sp_id = graph_sp["id"]
print(f"Microsoft Graph SP id: {graph_sp_id}")

# Step 4: Find the app role IDs we need
WANTED = {"User.Read.All", "User.ReadWrite.All", "Group.Read.All", "GroupMember.Read.All"}
role_map = {r["value"]: r["id"] for r in graph_sp.get("appRoles", []) if r["value"] in WANTED}
print(f"Role IDs found: {role_map}")

# Step 5: Check existing assignments to avoid duplicates
existing_resp = httpx.get(
    f"https://graph.microsoft.com/v1.0/servicePrincipals/{our_sp_id}/appRoleAssignments",
    headers=headers,
)
existing_resp.raise_for_status()
already_assigned = {a["appRoleId"] for a in existing_resp.json().get("value", [])}
print(f"Already assigned role IDs: {already_assigned}")

# Step 6: Grant missing permissions
for perm_name, role_id in role_map.items():
    if role_id in already_assigned:
        print(f"  SKIP {perm_name} (already granted)")
        continue
    assign_resp = httpx.post(
        f"https://graph.microsoft.com/v1.0/servicePrincipals/{our_sp_id}/appRoleAssignments",
        headers=headers,
        json={
            "principalId": our_sp_id,
            "resourceId": graph_sp_id,
            "appRoleId": role_id,
        },
    )
    if assign_resp.status_code in (200, 201):
        print(f"  GRANTED {perm_name}")
    else:
        print(f"  FAILED {perm_name}: {assign_resp.status_code} {assign_resp.text[:200]}")

print("Done.")
