# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
_NAME = "azure_blob"


async def upload(local_path: str, dest_key: str, config: dict) -> str:
    raise NotImplementedError(f"Storage backend '{_NAME}' is not yet implemented")


async def download(uri: str, local_path: str, config: dict) -> None:
    raise NotImplementedError(f"Storage backend '{_NAME}' is not yet implemented")


async def delete(uri: str, config: dict) -> None:
    raise NotImplementedError(f"Storage backend '{_NAME}' is not yet implemented")


async def put_file(dest_key: str, local_path: str, config: dict) -> str:
    raise NotImplementedError(f"Storage backend '{_NAME}' is not yet implemented")


async def get_file(uri: str, local_path: str, config: dict) -> None:
    raise NotImplementedError(f"Storage backend '{_NAME}' is not yet implemented")
