import httpx


def get_client(creds: dict) -> httpx.AsyncClient:
    url = creds["automate_url"].rstrip("/")
    return httpx.AsyncClient(
        base_url=f"{url}/api/v0",
        headers={"api-token": creds["api_token"], "Content-Type": "application/json"},
        timeout=30.0,
    )
