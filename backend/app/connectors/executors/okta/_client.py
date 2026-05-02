import httpx


def okta_headers(creds: dict) -> dict:
    return {
        "Authorization": f"SSWS {creds['api_token']}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def okta_base(creds: dict) -> str:
    return creds["org_url"].rstrip("/") + "/api/v1"
