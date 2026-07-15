# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import importlib
import pathlib
import sys
import types

import pytest

for pkg in [
    "boto3", "botocore", "botocore.exceptions",
    "google.cloud", "google.auth", "oci", "ldap3", "winrm",
    "kubernetes", "kubernetes.client", "kubernetes.config",
    "requests", "httpx", "paramiko", "ansible", "ansible.playbook",
    "azure", "azure.identity",
]:
    parts = pkg.split(".")
    for i in range(len(parts)):
        name = ".".join(parts[:i+1])
        if name not in sys.modules:
            sys.modules[name] = types.ModuleType(name)

# Add attribute stubs needed by _client.py modules at import time
_sentinel = type("_Stub", (), {"__init__": lambda self, *a, **kw: None})
sys.modules["paramiko"].SSHClient = _sentinel
sys.modules["paramiko"].AutoAddPolicy = _sentinel
sys.modules["httpx"].AsyncClient = _sentinel
sys.modules["httpx"].AsyncHTTPTransport = _sentinel
sys.modules["requests"].Session = _sentinel

FAMILIES = [
    ("sccm", "app.connectors.executors.sccm"),
    ("ansible", "app.connectors.executors.ansible"),
    ("ansible_local", "app.connectors.executors.ansible_local"),
    ("agent_tunnel", "app.connectors.executors.agent_tunnel"),
    ("ssh", "app.connectors.executors.ssh"),
    ("okta", "app.connectors.executors.okta"),
    ("github", "app.connectors.executors.github"),
    ("identity", "app.connectors.executors.identity"),
    ("nexplane_agent", "app.connectors.executors.nexplane_agent"),
]

EXCLUDED = {
    "nexplane_agent/aws_utils",
}


def _all_executor_params():
    params = []
    base = pathlib.Path(__file__).parent.parent.parent / "app" / "connectors" / "executors"
    for family, module_prefix in FAMILIES:
        family_dir = base / family
        for f in sorted(family_dir.glob("*.py")):
            if f.name.startswith("_") or f.name == "__init__.py":
                continue
            key = f"{family}/{f.stem}"
            if key in EXCLUDED:
                continue
            params.append(pytest.param(module_prefix, f.stem, id=key))
    return params


@pytest.mark.parametrize("module_prefix,name", _all_executor_params())
def test_rollback_capability_declared(module_prefix, name):
    mod = importlib.import_module(f"{module_prefix}.{name}")
    cap = getattr(mod, "ROLLBACK_CAPABILITY", None)
    assert cap in ("full", "irreversible"), (
        f"{name}: ROLLBACK_CAPABILITY={cap!r} — must be 'full' or 'irreversible'"
    )
    if cap == "irreversible":
        reason = getattr(mod, "ROLLBACK_REASON", None)
        assert reason and isinstance(reason, str) and len(reason) > 10, (
            f"{name}: ROLLBACK_REASON missing or too short"
        )
