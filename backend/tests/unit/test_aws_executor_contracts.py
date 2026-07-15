# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import importlib
import pathlib
import sys
import types

import pytest

for pkg in [
    "boto3", "botocore", "botocore.exceptions", "botocore.stub",
    "google.cloud", "google.auth", "oci", "ldap3", "winrm",
    "kubernetes", "requests", "httpx",
]:
    parts = pkg.split(".")
    for i in range(len(parts)):
        name = ".".join(parts[:i+1])
        if name not in sys.modules:
            sys.modules[name] = types.ModuleType(name)

EXECUTORS_DIR = pathlib.Path(__file__).parent.parent.parent / "app" / "connectors" / "executors" / "aws"


def _executor_names():
    return [
        f.stem for f in sorted(EXECUTORS_DIR.glob("*.py"))
        if not f.name.startswith("_") and f.name != "__init__.py"
    ]


@pytest.mark.parametrize("name", _executor_names())
def test_rollback_capability_declared(name):
    mod = importlib.import_module(f"app.connectors.executors.aws.{name}")
    cap = getattr(mod, "ROLLBACK_CAPABILITY", None)
    assert cap in ("full", "irreversible"), (
        f"{name}: ROLLBACK_CAPABILITY={cap!r} — must be 'full' or 'irreversible'"
    )
    if cap == "irreversible":
        reason = getattr(mod, "ROLLBACK_REASON", None)
        assert reason and isinstance(reason, str) and len(reason) > 10, (
            f"{name}: ROLLBACK_REASON missing or too short"
        )
