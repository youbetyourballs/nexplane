# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    repo = parameters["repo"]
    branch = parameters["branch"]
    dry_run = parameters.get("dry_run", False)

    if not creds:
        return {"action": "enforce_branch_protection", "repo": repo, "branch": branch, "applied": True, "mock": True}

    import httpx
    loop = asyncio.get_event_loop()

    def _call():
        org = creds["org"]
        headers = {
            "Authorization": f"Bearer {creds['token']}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        existing_resp = httpx.get(
            f"https://api.github.com/repos/{org}/{repo}/branches/{branch}/protection",
            headers=headers,
        )
        rollback_data = existing_resp.json() if existing_resp.status_code == 200 else None

        required_approvals = parameters.get("required_approving_review_count", 1)
        protection = {
            "required_status_checks": None,
            "enforce_admins": parameters.get("enforce_admins", True),
            "required_pull_request_reviews": {
                "required_approving_review_count": required_approvals,
                "dismiss_stale_reviews": True,
            } if parameters.get("require_pr_reviews", True) else None,
            "restrictions": None,
        }
        if parameters.get("require_status_checks"):
            protection["required_status_checks"] = {
                "strict": True,
                "contexts": parameters["require_status_checks"],
            }
        if parameters.get("restrict_push"):
            protection["restrictions"] = {
                "users": [],
                "teams": parameters["restrict_push"],
                "apps": [],
            }

        if not dry_run:
            put_resp = httpx.put(
                f"https://api.github.com/repos/{org}/{repo}/branches/{branch}/protection",
                headers=headers,
                json=protection,
            )
            put_resp.raise_for_status()
        return rollback_data

    rollback_data = await loop.run_in_executor(None, _call)
    return {
        "action": "enforce_branch_protection",
        "repo": repo,
        "branch": branch,
        "dry_run": dry_run,
        "applied": not dry_run,
        "rollback_data": rollback_data,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    rollback_data = execution_result.get("rollback_data")
    repo = parameters["repo"]
    branch = parameters["branch"]

    if not creds:
        return {"action": "restore_branch_protection", "repo": repo, "branch": branch, "mock": True}

    import httpx
    loop = asyncio.get_event_loop()

    def _restore():
        org = creds["org"]
        headers = {
            "Authorization": f"Bearer {creds['token']}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if rollback_data is None:
            httpx.delete(
                f"https://api.github.com/repos/{org}/{repo}/branches/{branch}/protection",
                headers=headers,
            )
            return {"restored": "deleted"}
        else:
            resp = httpx.put(
                f"https://api.github.com/repos/{org}/{repo}/branches/{branch}/protection",
                headers=headers,
                json=rollback_data,
            )
            resp.raise_for_status()
            return {"restored": "previous_policy"}

    result = await loop.run_in_executor(None, _restore)
    return {"action": "restore_branch_protection", "repo": repo, "branch": branch, **result}
