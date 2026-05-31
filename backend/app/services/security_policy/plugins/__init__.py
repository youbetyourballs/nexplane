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


# Register built-in plugins
from app.services.security_policy.plugins.seccomp import SECCOMP_PLUGIN  # noqa: E402
_register(SECCOMP_PLUGIN)
# apparmor registered in apparmor.py (Task 5)
# selinux: SP3
# network_policy: SP4
