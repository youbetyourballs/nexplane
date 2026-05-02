async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    issue_key = parameters["issue_key"]
    if not creds:
        return {"action": "add_comment", "issue_key": issue_key, "comment_id": "mock-comment-id"}
    from ._client import get_client
    body = {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": parameters["comment"]}]}]}
    async with get_client(creds) as client:
        resp = await client.post(f"/issue/{issue_key}/comment", json={"body": body})
        resp.raise_for_status()
        comment = resp.json()
    return {"action": "add_comment", "issue_key": issue_key, "comment_id": comment["id"]}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "comments cannot be automatically deleted"}
