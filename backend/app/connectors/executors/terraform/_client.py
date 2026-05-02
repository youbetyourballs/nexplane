import httpx


def get_client(creds: dict) -> httpx.AsyncClient:
    base = creds.get("base_url", "https://app.terraform.io").rstrip("/")
    return httpx.AsyncClient(
        base_url=f"{base}/api/v2",
        headers={"Authorization": f"Bearer {creds['api_token']}", "Content-Type": "application/vnd.api+json"},
        timeout=60.0,
    )
