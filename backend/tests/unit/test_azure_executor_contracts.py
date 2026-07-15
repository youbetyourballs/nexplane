# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import importlib
import pathlib
import sys
import types

import pytest

for pkg in [
    "boto3", "botocore", "google.cloud", "google.auth",
    "oci", "ldap3", "winrm", "kubernetes", "requests", "httpx",
    "azure", "azure.identity", "azure.mgmt", "azure.mgmt.compute",
    "azure.mgmt.network", "azure.mgmt.resource", "azure.mgmt.sql",
    "azure.mgmt.storage", "azure.mgmt.monitor", "azure.mgmt.dns",
    "azure.mgmt.authorization", "azure.mgmt.msi",
    "msgraph", "msgraph.core",
]:
    parts = pkg.split(".")
    for i in range(len(parts)):
        name = ".".join(parts[:i+1])
        if name not in sys.modules:
            sys.modules[name] = types.ModuleType(name)

FAMILIES = [
    ("azure", "app.connectors.executors.azure"),
    ("azure_ad", "app.connectors.executors.azure_ad"),
]

EXCLUDED = {"azure_ad_client"}


def _all_params():
    base = pathlib.Path(__file__).parent.parent.parent / "app" / "connectors" / "executors"
    params = []
    for family, module_prefix in FAMILIES:
        family_dir = base / family
        for f in sorted(family_dir.glob("*.py")):
            if f.name.startswith("_") or f.name == "__init__.py":
                continue
            if f.stem in EXCLUDED:
                continue
            params.append(pytest.param(module_prefix, f.stem, id=f"{family}/{f.stem}"))
    return params


@pytest.mark.parametrize("module_prefix,name", _all_params())
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
