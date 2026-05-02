import httpx


def get_rest_client(creds: dict) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=creds["base_url"].rstrip("/"),
        headers={"Authorization": f"Bearer {creds['token']}"},
        timeout=60.0,
        verify=False,
    )


def get_hec_client(creds: dict) -> httpx.AsyncClient:
    hec_url = creds.get("hec_url", creds["base_url"].replace(":8089", ":8088"))
    return httpx.AsyncClient(
        base_url=hec_url,
        headers={"Authorization": f"Splunk {creds['token']}"},
        timeout=30.0,
        verify=False,
    )
