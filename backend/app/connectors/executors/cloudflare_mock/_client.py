import httpx

CF_BASE = "https://api.cloudflare.com/client/v4"


def cf_headers(creds: dict) -> dict:
    return {"Authorization": f"Bearer {creds['api_token']}", "Content-Type": "application/json"}


async def cf_get(path: str, creds: dict) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{CF_BASE}{path}", headers=cf_headers(creds))
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise RuntimeError(f"Cloudflare error: {data.get('errors')}")
        return data


async def cf_post(path: str, body: dict, creds: dict) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{CF_BASE}{path}", headers=cf_headers(creds), json=body)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise RuntimeError(f"Cloudflare error: {data.get('errors')}")
        return data


async def cf_put(path: str, body: dict, creds: dict) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.put(f"{CF_BASE}{path}", headers=cf_headers(creds), json=body)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise RuntimeError(f"Cloudflare error: {data.get('errors')}")
        return data


async def cf_delete(path: str, creds: dict) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.delete(f"{CF_BASE}{path}", headers=cf_headers(creds))
        resp.raise_for_status()
        return resp.json()
