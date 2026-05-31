# backend/app/services/security_policy/plugins/__init__.py
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.security_policy.plugins.base import PolicyPlugin

_REGISTRY: dict[str, "PolicyPlugin"] = {}


def _register(plugin: "PolicyPlugin") -> None:
    _REGISTRY[plugin.policy_type] = plugin


def get_plugin(policy_type: str) -> "PolicyPlugin":
    plugin = _REGISTRY.get(policy_type)
    if plugin is None:
        raise ValueError(f"Unsupported policy_type: {policy_type!r}. Known: {sorted(_REGISTRY)}")
    return plugin
