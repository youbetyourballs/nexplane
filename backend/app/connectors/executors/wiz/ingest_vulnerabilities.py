QUERY = """
query Vulnerabilities($first: Int) {
  vulnerabilityFindings(first: $first) {
    nodes { id name severity cvss cveId status affectedAsset { id name } }
  }
}
"""


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "ingest_vulnerabilities", "vulnerabilities": [], "count": 0}
    from ._client import get_access_token, graphql_query
    token = await get_access_token(creds)
    data = await graphql_query(token, QUERY, {"first": 500})
    vulns = data.get("data", {}).get("vulnerabilityFindings", {}).get("nodes", [])
    return {"action": "ingest_vulnerabilities", "vulnerabilities": vulns, "count": len(vulns)}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "ingest has no rollback"}
