# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
_NAME = "lvm_snapshot"


async def backup(params: dict, asset_ids: list, connector) -> dict:
    raise NotImplementedError(f"Capture strategy '{_NAME}' is not yet implemented")


async def rollback(params: dict, execution_result: dict, connector) -> dict:
    raise NotImplementedError(f"Capture strategy '{_NAME}' is not yet implemented")
