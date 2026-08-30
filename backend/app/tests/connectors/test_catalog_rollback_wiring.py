import json
import pytest
from pathlib import Path

# From /app/app/tests/connectors/test_catalog_rollback_wiring.py, go up 4 to /app, then to catalog
CATALOG_PATH = Path(__file__).parent.parent.parent.parent / "app/connectors/catalog/nexplane_agent.json"

MUST_HAVE_ROLLBACK = [
    "agent_os_upgrade",
    "db_major_version_upgrade",
    "elasticsearch_upgrade",
    "opensearch_upgrade",
    "rabbitmq_upgrade",
    "windows_os_upgrade",
    "java_runtime_upgrade",
    "python_runtime_upgrade",
    "emergency_user_lockout",
    "user_suspension",
    "user_scope_reduction",
]


def test_rollback_action_wired_for_all_required_executors():
    catalog = json.loads(CATALOG_PATH.read_text())
    actions = {a["action_id"]: a for a in catalog.get("actions", [])}
    missing = [aid for aid in MUST_HAVE_ROLLBACK if "rollback_action" not in actions.get(aid, {})]
    assert not missing, f"Missing rollback_action in catalog for: {missing}"


def test_rollback_action_values_reference_valid_action_ids():
    catalog = json.loads(CATALOG_PATH.read_text())
    actions = {a["action_id"]: a for a in catalog.get("actions", [])}
    # Only validate the required actions have valid rollback_action references
    invalid = [
        (aid, actions[aid].get("rollback_action"))
        for aid in MUST_HAVE_ROLLBACK
        if actions[aid].get("rollback_action") not in actions
    ]
    assert not invalid, f"rollback_action values point to nonexistent action_ids: {invalid}"
