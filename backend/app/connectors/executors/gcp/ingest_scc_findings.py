import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "ingest_scc_findings", "findings": [], "count": 0}
    from ._client import get_credentials, get_project_id
    from google.cloud import securitycenter_v1
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()
    client = securitycenter_v1.SecurityCenterClient(credentials=credentials)
    org_name = f"projects/{project}/sources/-"
    findings_list = await loop.run_in_executor(None, lambda: list(client.list_findings(parent=org_name)))
    findings = [{"name": f.finding.name, "category": f.finding.category, "severity": str(f.finding.severity), "state": str(f.finding.state)} for f in findings_list]
    return {"action": "ingest_scc_findings", "findings": findings, "count": len(findings)}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "ingest has no rollback"}
