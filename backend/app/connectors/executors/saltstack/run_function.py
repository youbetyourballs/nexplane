# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

ALLOWED_FUNCTIONS = {"sys.doc", "test.ping", "pkg.list_pkgs", "service.status"}

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    fun = parameters.get("function")
    target = parameters.get("target", "*")
    if fun not in ALLOWED_FUNCTIONS:
        return {"action": "run_function", "error": f"Function '{fun}' not in allowlist: {ALLOWED_FUNCTIONS}"}
    if not creds:
        return {"action": "run_function", "target": target, "function": fun, "result": {"mock-minion": True}}
    from ._client import get_token, get_client
    token = await get_token(creds)
    async with get_client(creds, token) as client:
        resp = await client.post("/", json=[{"client": "local", "tgt": target, "fun": fun}])
        resp.raise_for_status()
        result = resp.json().get("return", [{}])[0]
    return {"action": "run_function", "target": target, "function": fun, "result": result}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "function execution cannot be reversed"}
