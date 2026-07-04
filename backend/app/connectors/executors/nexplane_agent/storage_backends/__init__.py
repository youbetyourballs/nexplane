# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
from types import ModuleType

from app.connectors.executors.nexplane_agent.storage_backends import s3

_STUB_NAMES = ("gcs", "azure_blob", "oci_object_storage", "nfs", "local")

_REGISTRY: dict[str, ModuleType] = {
    "s3": s3,
}

# Lazy-register stubs so they appear in the registry but raise on use
for _name in _STUB_NAMES:
    import importlib as _importlib
    _mod = _importlib.import_module(
        f"app.connectors.executors.nexplane_agent.storage_backends.{_name}"
    )
    _REGISTRY[_name] = _mod


def get_backend(storage_type: str) -> ModuleType:
    if storage_type not in _REGISTRY:
        raise ValueError(f"Unknown storage backend: '{storage_type}'. Known: {list(_REGISTRY)}")
    return _REGISTRY[storage_type]
