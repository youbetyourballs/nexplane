import httpx
import base64


def get_client(creds: dict) -> httpx.AsyncClient:
    credentials = base64.b64encode(f"{creds['username']}:{creds['password']}".encode()).decode()
    return httpx.AsyncClient(
        base_url=creds["api_url"].rstrip("/"),
        headers={"Authorization": f"Basic {credentials}", "X-Requested-With": "Nexplane"},
        timeout=60.0,
    )
