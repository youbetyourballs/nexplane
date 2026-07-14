# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from typing import Protocol, runtime_checkable


# Every executor module MUST declare one of:
#   ROLLBACK_CAPABILITY = "full"       — pre-state captured; rollback() reconstitutes
#   ROLLBACK_CAPABILITY = "irreversible" — no automated undo possible; requires ROLLBACK_REASON = "..."
# Missing or invalid ROLLBACK_CAPABILITY causes planning to hard-fail (HTTP 400).
@runtime_checkable
class ExecutorProtocol(Protocol):
    async def execute(self, parameters: dict, asset_ids: list, connector) -> dict: ...
    async def rollback(self, parameters: dict, execution_result: dict, connector) -> dict: ...
