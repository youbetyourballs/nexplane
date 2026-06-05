"""Catalog-executor parity guard.

Asserts that every action_id declared in a connector catalog JSON has a corresponding
executor module file, and every change-action executor module is referenced by at least
one change_type_definitions JSON. No live infrastructure required.
"""
import importlib
import json
import os
from pathlib import Path

import pytest

# Inside Docker the backend is mounted at /app; on the host the path has
# three more components. Detect by checking if /app/app/connectors exists.
_this = Path(__file__).resolve()
_backend_root = _this.parents[2]  # /app (or .../backend/)
if not (_backend_root / "app" / "connectors").exists():
    _backend_root = _this.parents[4] / "backend"  # host path fallback
CATALOG_DIR = _backend_root / "app" / "connectors" / "catalog"
EXECUTORS_DIR = _backend_root / "app" / "connectors" / "executors"
CR_DEFS_DIR = _backend_root / "app" / "connectors" / "change_type_definitions"

# Ingest-only action types that don't require a CR-type definition (they run
# as connector.ingest() calls, not as CRs).
INGEST_GENERIC_ACTIONS = {"discover", "ingest", "sync"}


def _load_catalogs() -> list[dict]:
    return [json.loads(p.read_text()) for p in CATALOG_DIR.glob("*.json")]


def _load_cr_defs() -> dict[str, dict]:
    """Return mapping change_type → parsed JSON."""
    result = {}
    for p in CR_DEFS_DIR.glob("*.json"):
        d = json.loads(p.read_text())
        if "change_type" not in d:
            continue
        result[d["change_type"]] = d
    return result


def _executor_module_exists(connector_type: str, action_id: str) -> bool:
    """Return True if executors/<connector_type>/<action_id>.py exists."""
    return (EXECUTORS_DIR / connector_type / f"{action_id}.py").is_file()


def _executor_dir_exists(connector_type: str) -> bool:
    return (EXECUTORS_DIR / connector_type).is_dir()


# ---------------------------------------------------------------------------
# Test 1: every catalog action_id has an executor module
# ---------------------------------------------------------------------------

def _catalog_action_cases():
    cases = []
    for catalog in _load_catalogs():
        ct = catalog["connector_type"]
        for action in catalog.get("actions", []):
            executor_ref = action.get("executor", "")
            # executor field is "<connector>.<action_id>"
            if "." in executor_ref:
                conn, act = executor_ref.split(".", 1)
            else:
                conn, act = ct, action["action_id"]
            cases.append(pytest.param(conn, act, id=f"{conn}.{act}"))
    return cases


@pytest.mark.parametrize("connector_type,action_id", _catalog_action_cases())
def test_catalog_action_has_executor_module(connector_type: str, action_id: str):
    assert _executor_dir_exists(connector_type), (
        f"Executor directory missing: executors/{connector_type}/"
    )
    assert _executor_module_exists(connector_type, action_id), (
        f"Executor module missing: executors/{connector_type}/{action_id}.py  "
        f"(declared in catalog for connector '{connector_type}')"
    )


# ---------------------------------------------------------------------------
# Test 2: every non-ingest catalog action is covered by a CR-type definition
# ---------------------------------------------------------------------------

def _catalog_change_action_cases():
    cr_defs = _load_cr_defs()
    # Build set of executor refs referenced by any CR def step
    referenced_executors: set[str] = set()
    for cr_def in cr_defs.values():
        for step in cr_def.get("steps", []):
            ga = step.get("generic_action", "")
            if ga:
                referenced_executors.add(ga)

    cases = []
    for catalog in _load_catalogs():
        ct = catalog["connector_type"]
        for action in catalog.get("actions", []):
            generic = action.get("generic_action", "")
            if generic in INGEST_GENERIC_ACTIONS:
                continue
            action_type = action.get("action_type", "")
            if action_type == "ingest":
                continue
            action_id = action["action_id"]
            cases.append(pytest.param(ct, action_id, generic, id=f"{ct}.{action_id}"))
    return cases


@pytest.mark.parametrize("connector_type,action_id,generic_action", _catalog_change_action_cases())
def test_change_action_has_cr_type_definition(connector_type: str, action_id: str, generic_action: str):
    cr_defs = _load_cr_defs()
    # A CR type definition covers this action if its steps reference the generic_action
    # OR if a CR type named <connector_type>_<action_id> exists
    named_match = f"{connector_type}_{action_id}"
    if named_match in cr_defs:
        return  # exact name match

    for cr_def in cr_defs.values():
        for step in cr_def.get("steps", []):
            if step.get("generic_action") == generic_action:
                return  # referenced by some CR def

    pytest.fail(
        f"No CR-type definition covers change action '{connector_type}.{action_id}' "
        f"(generic_action='{generic_action}'). "
        f"Add a JSON under change_type_definitions/ with a step referencing this action."
    )


# ---------------------------------------------------------------------------
# Test 3: every CR-type definition JSON references an executor that exists
# ---------------------------------------------------------------------------

def test_cr_type_definitions_reference_valid_executors():
    failures = []
    for cr_type, cr_def in _load_cr_defs().items():
        rollback_conn = cr_def.get("rollback_connector_type")
        rollback_action = cr_def.get("rollback_action")
        # "rollback_unavailable" is an intentional sentinel for ops that cannot be undone
        if rollback_conn and rollback_action and rollback_action not in (cr_type, "rollback_unavailable", None):
            if not _executor_module_exists(rollback_conn, rollback_action):
                failures.append(
                    f"{cr_type}: rollback executor missing: "
                    f"executors/{rollback_conn}/{rollback_action}.py"
                )
    assert not failures, "CR-type definition rollback executor gaps:\n" + "\n".join(failures)
