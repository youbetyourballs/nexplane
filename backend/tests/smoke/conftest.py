# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Pytest configuration for smoke tests.
Adds the smoke directory to sys.path for relative imports of smoke_helpers.
"""
import sys
from pathlib import Path

# Add the smoke test directory to sys.path so smoke_helpers can be imported
smoke_dir = Path(__file__).parent
if str(smoke_dir) not in sys.path:
    sys.path.insert(0, str(smoke_dir))
