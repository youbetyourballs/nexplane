from __future__ import annotations
import importlib
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
    def __init__(self, catalog_dir: pathlib.Path):
        self._catalog: dict[str, list[dict]] = {}
        self._raw: dict[str, dict] = {}
        self._generic_index: dict[str, list[ActionOption]] = {}
        self._load(catalog_dir)

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
        for key in self._generic_index:
            self._generic_index[key].sort(key=lambda o: o.execution_tier)

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

    def get_executor(self, connector_type: str, action_id: str) -> types.ModuleType:
        """Resolves executor reference to an importable module.
        Caller accesses execute() and rollback() as module attributes.
        Raises ImportError if the module does not exist yet.
        """
        action_def = self.get_action_def(connector_type, action_id)
        executor_ref = action_def.get("executor", "")
        parts = executor_ref.split(".")
        if len(parts) != 2:
            raise ValueError(f"Invalid executor reference '{executor_ref}' — expected 'connector.module'")
        module_path = f"app.connectors.executors.{parts[0]}.{parts[1]}"
        try:
            return importlib.import_module(module_path)
        except ModuleNotFoundError as exc:
            raise ImportError(f"Executor module '{module_path}' not found: {exc}") from exc


_catalog_service: ActionCatalogService | None = None


def init_catalog_service(catalog_dir: pathlib.Path) -> None:
    global _catalog_service
    _catalog_service = ActionCatalogService(catalog_dir)


def get_catalog_service() -> ActionCatalogService:
    global _catalog_service
    if _catalog_service is None:
        default_dir = pathlib.Path(__file__).parent / "catalog"
        init_catalog_service(default_dir)
    return _catalog_service
