import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    repo = parameters["repo"]

    if not creds:
        return {"action": "archive_repo", "repo": repo, "archived": True, "rollback_data": {"archived": False}, "mock": True}

    import github as gh
    loop = asyncio.get_event_loop()

    def _call():
        g = gh.Github(creds["token"])
        org = creds["org"]
        repository = g.get_repo(f"{org}/{repo}")
        was_archived = repository.archived
        repository.edit(archived=True)
        return was_archived

    was_archived = await loop.run_in_executor(None, _call)
    return {
        "action": "archive_repo",
        "repo": repo,
        "archived": True,
        "rollback_data": {"archived": was_archived},
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    repo = parameters["repo"]

    if not creds:
        return {"action": "unarchive_repo", "repo": repo, "mock": True}

    import github as gh
    loop = asyncio.get_event_loop()

    def _unarchive():
        g = gh.Github(creds["token"])
        repository = g.get_repo(f"{creds['org']}/{repo}")
        repository.edit(archived=False)

    await loop.run_in_executor(None, _unarchive)
    return {"action": "unarchive_repo", "repo": repo, "rolled_back": True}
