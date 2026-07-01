# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations
"""Falco — write a local rule to the host and restart the Falco service."""
from datetime import datetime, timezone
from typing import Optional
import textwrap


def _build_rule_yaml(rule_name: str, condition: str,
                     output: Optional[str] = None, priority: str = "WARNING") -> str:
    out = output or f"Falco rule triggered: {rule_name} (proc=%proc.name user=%user.name)"
    return textwrap.dedent(f"""\
        - rule: {rule_name}
          desc: Nexplane-managed rule — {rule_name}
          condition: {condition}
          output: "{out}"
          priority: {priority}
          tags: [nexplane]
    """)


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    from ._client import get_falco_ssm_client

    rule_name = parameters.get("rule_name", "")
    rule_condition = parameters.get("rule_condition", "")
    if not rule_name or not rule_condition:
        raise ValueError("rule_name and rule_condition are required")

    priority = parameters.get("priority", "WARNING")
    output_msg = parameters.get("output", None)

    client = get_falco_ssm_client(connector)
    if client is None:
        return {
            "action": "falco_policy_update",
            "status": "skipped",
            "reason": "no_falco_ssm_credentials",
            "rule_name": rule_name,
        }

    rule_yaml = _build_rule_yaml(rule_name, rule_condition, output_msg, priority)
    client.write_rule(rule_yaml)
    client.restart_falco()

    return {
        "action": "falco_policy_update",
        "rule_name": rule_name,
        "rule_condition": rule_condition,
        "priority": priority,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from ._client import get_falco_ssm_client

    rule_name = execution_result.get("rule_name") or parameters.get("rule_name", "")
    if not rule_name:
        return {"rolled_back": False, "reason": "no_rule_name_in_result"}

    client = get_falco_ssm_client(connector)
    if client is None:
        return {"rolled_back": False, "reason": "no_falco_ssm_credentials"}

    client.remove_rule(rule_name)
    client.restart_falco()

    return {
        "rolled_back": True,
        "rule_name": rule_name,
        "action": "falco_policy_update_rollback",
    }
