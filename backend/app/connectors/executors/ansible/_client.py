import httpx


def get_client(creds: dict) -> httpx.AsyncClient:
    url = creds["controller_url"].rstrip("/")
    return httpx.AsyncClient(
        base_url=f"{url}/api/v2",
        headers={"Authorization": f"Bearer {creds['api_token']}", "Content-Type": "application/json"},
        timeout=60.0,
    )
