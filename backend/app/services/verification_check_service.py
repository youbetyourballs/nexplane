# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations
import asyncio
from typing import Any

import httpx


async def run_verification_checks(
    checks: list[dict[str, Any]],
    asset_ids: list[str],
) -> list[dict[str, Any]]:
    results = []
    for check in checks:
        result = await _run_single_check(check, asset_ids)
        results.append(result)
    return results


async def _run_single_check(check: dict, asset_ids: list[str]) -> dict:
    check_type = check.get("type", "")
    try:
        if check_type == "http":
            return await _http_check(check)
        elif check_type == "port":
            return await _port_check(check)
        else:
            return {"type": check_type, "passed": False, "detail": f"Unknown check type: {check_type}"}
    except Exception as e:
        return {"type": check_type, "passed": False, "detail": str(e)}


async def _http_check(check: dict) -> dict:
    url = check["url"]
    expected = check.get("expected_status", 200)
    body_contains = check.get("body_contains")
    async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
        resp = await client.get(url)
    passed = resp.status_code == expected
    detail = f"HTTP {resp.status_code}"
    if body_contains and body_contains not in resp.text:
        passed = False
        detail += f" — body did not contain '{body_contains}'"
    return {"type": "http", "url": url, "passed": passed, "detail": detail}


async def _port_check(check: dict) -> dict:
    host = check["host"]
    port = int(check["port"])
    expect_open = check.get("expected_open", True)
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=5.0
        )
        writer.close()
        is_open = True
    except Exception:
        is_open = False
    passed = is_open == expect_open
    return {
        "type": "port", "host": host, "port": port,
        "passed": passed, "detail": f"port {'open' if is_open else 'closed'}"
    }
