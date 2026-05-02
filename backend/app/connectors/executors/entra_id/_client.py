import msal
import httpx

GRAPH_URL = "https://graph.microsoft.com/v1.0"


async def get_access_token(creds: dict) -> str:
    app = msal.ConfidentialClientApplication(
        creds["client_id"],
        authority=f"https://login.microsoftonline.com/{creds['tenant_id']}",
        client_credential=creds["client_secret"],
    )
    result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" not in result:
        raise RuntimeError(f"Failed to acquire token: {result.get('error_description')}")
    return result["access_token"]


def get_graph_client(token: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=GRAPH_URL,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )
