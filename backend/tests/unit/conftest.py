# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

# Pre-import real third-party packages so that executor-contract test files
# (which inject stub modules via sys.modules for packages not yet imported)
# cannot replace the real packages when they run before tests that need them.
import importlib

for _pkg in ("requests", "hvac", "boto3", "botocore", "httpx", "paramiko"):
    try:
        importlib.import_module(_pkg)
    except ImportError:
        pass
