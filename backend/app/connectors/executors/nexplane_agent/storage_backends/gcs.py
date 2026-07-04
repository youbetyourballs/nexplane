# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
_NAME = "gcs"


async def put(key: str, data: bytes, config: dict) -> str:
    raise NotImplementedError(f"Storage backend '{_NAME}' is not yet implemented")


async def put_file(key: str, local_path: str, config: dict) -> str:
    raise NotImplementedError(f"Storage backend '{_NAME}' is not yet implemented")


async def delete_prefix(prefix: str, config: dict) -> dict:
    raise NotImplementedError(f"Storage backend '{_NAME}' is not yet implemented")


async def delete(uri: str, config: dict) -> None:
    raise NotImplementedError(f"Storage backend '{_NAME}' is not yet implemented")
