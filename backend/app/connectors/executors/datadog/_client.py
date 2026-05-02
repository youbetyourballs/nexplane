import httpx


def get_client(creds: dict) -> httpx.AsyncClient:
    site = creds.get("site", "datadoghq.com")
    return httpx.AsyncClient(
        base_url=f"https://api.{site}/api/v2",
        headers={"DD-API-KEY": creds["api_key"], "DD-APPLICATION-KEY": creds["app_key"], "Content-Type": "application/json"},
        timeout=30.0,
    )


def get_v1_client(creds: dict) -> httpx.AsyncClient:
    site = creds.get("site", "datadoghq.com")
    return httpx.AsyncClient(
        base_url=f"https://api.{site}/api/v1",
        headers={"DD-API-KEY": creds["api_key"], "DD-APPLICATION-KEY": creds["app_key"]},
        timeout=30.0,
    )
