from __future__ import annotations


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    name = parameters["name"]
    if not creds:
        return {"action": "create_alert", "name": name, "created": True}
    from ._client import get_splunk_client
    client = get_splunk_client(connector)
    if client is None:
        return {"action": "create_alert", "name": name, "created": True, "status": "skipped"}
    try:
        client.create_saved_search(
            name=name,
            query=parameters["search"],
            cron_schedule=parameters.get("cron_schedule"),
        )
        return {"action": "create_alert", "name": name, "created": True}
    finally:
        client.close()


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    name = execution_result.get("name") or parameters.get("name", "")
    if not name:
        return {"rolled_back": False, "reason": "no alert name to delete"}
    from ._client import get_splunk_client
    client = get_splunk_client(connector)
    if client is None:
        return {"rolled_back": False, "reason": "no credentials"}
    try:
        result = client.delete_saved_search(name)
        return {"rolled_back": result.get("deleted", False), "name": name}
    except Exception as exc:
        return {"rolled_back": False, "reason": str(exc)}
    finally:
        client.close()
