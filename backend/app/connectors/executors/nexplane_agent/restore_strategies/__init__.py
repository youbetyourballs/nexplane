# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import importlib
from types import ModuleType


class IrreversibleOperationError(Exception):
    pass


_PKG = "app.connectors.executors.nexplane_agent.restore_strategies"

_STRATEGY_NAMES = (
    "launch_ami",
    "in_place",
    "import_image",
    "file_restore_to_path",
    "database_restore",
    "storage_restore",
)

_REGISTRY: dict = {}


def get_strategy(restore_strategy: str) -> ModuleType:
    if restore_strategy not in _REGISTRY:
        if restore_strategy in _STRATEGY_NAMES:
            _REGISTRY[restore_strategy] = importlib.import_module(f"{_PKG}.{restore_strategy}")
        else:
            raise ValueError(
                f"Unknown restore strategy: '{restore_strategy}'. Known: {list(_STRATEGY_NAMES)}"
            )
    return _REGISTRY[restore_strategy]
