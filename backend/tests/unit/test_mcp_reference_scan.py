# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import app.mcp_tools.reference_scan as module


def test_mcp_reference_scan_tools_registered():
    for tool_name in [
        "scan_for_references",
        "get_scan_results",
        "list_reference_exceptions",
        "resolve_reference_exception",
        "reattempt_reference_triage",
        "dismiss_reference_exception",
    ]:
        assert hasattr(module, tool_name), f"Missing MCP tool: {tool_name}"
