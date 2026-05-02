import httpx

AUTH_URL = "https://auth.app.wiz.io/oauth/token"
GRAPHQL_URL = "https://api.us1.app.wiz.io/graphql"


async def get_access_token(creds: dict) -> str:
    async with httpx.AsyncClient() as client:
        resp = await client.post(AUTH_URL, data={
            "grant_type": "client_credentials",
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
            "audience": "wiz-api",
        })
        resp.raise_for_status()
        return resp.json()["access_token"]


async def graphql_query(token: str, query: str, variables: dict = None) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            GRAPHQL_URL,
            headers={"Authorization": f"Bearer {token}"},
            json={"query": query, "variables": variables or {}},
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()
