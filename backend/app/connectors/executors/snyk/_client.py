import httpx


def get_client(creds: dict) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="https://api.snyk.io/rest",
        headers={"Authorization": f"token {creds['api_token']}", "Content-Type": "application/json"},
        timeout=30.0,
    )
