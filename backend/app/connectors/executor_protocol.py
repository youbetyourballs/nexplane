# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from typing import Protocol, runtime_checkable


@runtime_checkable
class ExecutorProtocol(Protocol):
    async def execute(self, parameters: dict, asset_ids: list, connector) -> dict: ...
    async def rollback(self, parameters: dict, execution_result: dict, connector) -> dict: ...
