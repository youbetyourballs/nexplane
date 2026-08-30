import json
import pytest
from pathlib import Path

CATALOG_PATH = Path(__file__).parent.parent.parent / "connectors/catalog/nexplane_agent.json"

READ_ONLY_ACTIONS = [
    "trivy_scan",
    "lynis_audit",
    "openscap_scan",
    "ssl_cert_inspect",
    "sudoers_audit",
    "suid_scan",
    "authorized_keys_audit",
    "check_compliance",
    "check_fleet_health",
    "agent_scan_host_applications",
]


def test_read_only_scans_not_tagged_as_change():
    catalog = json.loads(CATALOG_PATH.read_text())
    actions = {a["action_id"]: a for a in catalog.get("actions", [])}
    wrong = [
        aid for aid in READ_ONLY_ACTIONS
        if actions.get(aid, {}).get("action_type") == "change"
    ]
    assert not wrong, f"Read-only actions incorrectly tagged as 'change': {wrong}"


def test_read_only_scans_tagged_as_read():
    catalog = json.loads(CATALOG_PATH.read_text())
    actions = {a["action_id"]: a for a in catalog.get("actions", [])}
    not_read = [
        (aid, actions.get(aid, {}).get("action_type"))
        for aid in READ_ONLY_ACTIONS
        if actions.get(aid, {}).get("action_type") != "read"
    ]
    assert not not_read, f"Actions not set to 'read': {not_read}"
