# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations
import importlib
import importlib.util
import json
import pathlib
import types
from dataclasses import dataclass


@dataclass
class ActionOption:
    connector_type: str
    action_id: str
    action_def: dict
    execution_tier: int


class ActionCatalogService:
    def __init__(self, catalog_dir: pathlib.Path, commercial_catalog_dir: pathlib.Path | None = None):
        self._catalog: dict[str, list[dict]] = {}
        self._raw: dict[str, dict] = {}
        self._generic_index: dict[str, list[ActionOption]] = {}
        self._commercial_catalog_dir = commercial_catalog_dir
        self._load(catalog_dir)
        # Load commercial catalog second so commercial entries override core entries for the same connector_type
        if commercial_catalog_dir is not None and commercial_catalog_dir.exists():
            self._load(commercial_catalog_dir)
        # Sort all generic action options by execution tier once after both loads complete
        for key in self._generic_index:
            self._generic_index[key].sort(key=lambda o: o.execution_tier)

    def _load(self, catalog_dir: pathlib.Path) -> None:
        for json_file in sorted(catalog_dir.glob("*.json")):
            data = json.loads(json_file.read_text())
            connector_type = data["connector_type"]
            actions = data.get("actions", [])
            self._catalog[connector_type] = actions
            self._raw[connector_type] = data
            for action_def in actions:
                generic = action_def.get("generic_action")
                option = ActionOption(
                    connector_type=connector_type,
                    action_id=action_def["action_id"],
                    action_def=action_def,
                    execution_tier=action_def.get("execution_tier", 99),
                )
                if generic:
                    self._generic_index.setdefault(generic, []).append(option)

    def get_options_for_action(
        self,
        generic_action: str,
        asset_types: list[str] | None = None,
        active_connector_types: list[str] | None = None,
    ) -> list[ActionOption]:
        options = list(self._generic_index.get(generic_action, []))
        if asset_types is not None:
            options = [
                o for o in options
                if any(t in o.action_def.get("applicable_asset_types", []) for t in asset_types)
            ]
        if active_connector_types is not None:
            options = [o for o in options if o.connector_type in active_connector_types]
        return options

    def get_action_def(self, connector_type: str, action_id: str) -> dict:
        actions = self._catalog.get(connector_type, [])
        for action in actions:
            if action["action_id"] == action_id:
                return action
        raise KeyError(f"Action '{action_id}' not found in connector '{connector_type}'")

    def list_connector_types(self) -> list[str]:
        """Return list of all connector types in catalog."""
        return list(self._catalog.keys())

    def list_generic_actions(self, action_type: str | None = None) -> list[str]:
        if action_type is None:
            return list(self._generic_index.keys())
        result = set()
        for generic, options in self._generic_index.items():
            if any(o.action_def.get("action_type") == action_type for o in options):
                result.add(generic)
        return list(result)

    def get_connector_catalog(self, connector_type: str) -> dict:
        """Return the full raw catalog dict for a connector type."""
        if connector_type not in self._raw:
            raise KeyError(f"Connector type '{connector_type}' not found in catalog")
        return self._raw[connector_type]

    def _load_commercial_executor(self, executor_ref: str) -> types.ModuleType:
        """Load a commercial executor from the filesystem.

        executor_ref has format 'commercial.{connector_type}.{module_name}'
        Resolves to: {commercial_catalog_dir.parent}/executors/{connector_type}/{module_name}.py
        """
        parts = executor_ref.split(".")
        if len(parts) != 3 or parts[0] != "commercial":
            raise ValueError(f"Invalid commercial executor reference '{executor_ref}' — expected 'commercial.connector_type.module_name'")

        connector_type = parts[1]
        module_name = parts[2]

        if self._commercial_catalog_dir is None:
            raise ImportError(f"Commercial executor '{executor_ref}' cannot be loaded: no commercial catalog dir configured")

        # Resolve to: {commercial_catalog_dir.parent}/executors/{connector_type}/{module_name}.py
        executor_file = self._commercial_catalog_dir.parent / "executors" / connector_type / f"{module_name}.py"

        if not executor_file.exists():
            raise ImportError(f"Commercial executor file '{executor_file}' not found for executor ref '{executor_ref}'")

        spec = importlib.util.spec_from_file_location(executor_ref, executor_file)
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not create module spec for '{executor_file}'")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def get_executor(self, connector_type: str, action_id: str) -> types.ModuleType:
        """Resolves executor reference to an importable module.
        Caller accesses execute() and rollback() as module attributes.
        Raises ImportError if the module does not exist yet.

        Handles two types of executors:
        - Standard: 'connector.module' → app.connectors.executors.connector.module (via importlib.import_module)
        - Commercial: 'commercial.connector_type.module_name' → filesystem (via importlib.util)
        """
        action_def = self.get_action_def(connector_type, action_id)
        executor_ref = action_def.get("executor", "")

        if executor_ref.startswith("commercial."):
            return self._load_commercial_executor(executor_ref)

        parts = executor_ref.split(".")
        if len(parts) != 2:
            raise ValueError(f"Invalid executor reference '{executor_ref}' — expected 'connector.module'")
        module_path = f"app.connectors.executors.{parts[0]}.{parts[1]}"
        try:
            return importlib.import_module(module_path)
        except ModuleNotFoundError as exc:
            raise ImportError(f"Executor module '{module_path}' not found: {exc}") from exc


    def list_all_actions(self) -> list[dict]:
        """Every action across all connector types, each tagged with its connector_type."""
        out: list[dict] = []
        for connector_type, actions in self._catalog.items():
            for action in actions:
                out.append({"connector_type": connector_type, **action})
        return out


# Alias for external consumers (e.g. discovery API, tests)
CatalogService = ActionCatalogService

_catalog_service: ActionCatalogService | None = None


def init_catalog_service(catalog_dir: pathlib.Path, commercial_catalog_dir: pathlib.Path | None = None) -> None:
    global _catalog_service
    _catalog_service = ActionCatalogService(catalog_dir, commercial_catalog_dir=commercial_catalog_dir)


def get_catalog_service() -> ActionCatalogService:
    global _catalog_service
    if _catalog_service is None:
        default_dir = pathlib.Path(__file__).parent / "catalog"
        init_catalog_service(default_dir)
    return _catalog_service
