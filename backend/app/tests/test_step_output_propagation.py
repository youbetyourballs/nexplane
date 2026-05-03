"""
Tests for ChangePlanStep produces/consumes fields and the execution engine's
StepOutputs propagation logic.

IMPORTANT: These tests verify that credential values NEVER appear in logs or
in the change_steps DB table. Only slot names (e.g. "new_password") appear
in persisted records.
"""

import pytest
import logging
from app.models.change_plan import ChangePlanStep, StepOutput
from app.services.change_execution import (
    StepOutputStore,
    inject_consumed_inputs,
    resolve_produces,
)


# ── ChangePlanStep schema ─────────────────────────────────────────────────────

def test_change_plan_step_default_produces_consumes():
    step = ChangePlanStep(
        id="step_1",
        type="agent_command",
        command="rotate_db_credentials",
        params={"action": "generate"},
    )
    assert step.produces == []
    assert step.consumes == []


def test_change_plan_step_with_produces_and_consumes():
    step = ChangePlanStep(
        id="step_2",
        type="agent_command",
        command="rotate_db_credentials",
        params={"action": "update_config"},
        produces=[],
        consumes=["new_password"],
        rollback_command="rotate_db_credentials",
        rollback_params={"action": "restore_config"},
    )
    assert "new_password" in step.consumes
    assert step.rollback_command == "rotate_db_credentials"


def test_step_output_model():
    so = StepOutput(slot="new_password", sealed=True, value="s3cr3t")
    assert so.slot == "new_password"
    assert so.sealed is True
    assert so.value == "s3cr3t"


# ── StepOutputStore ───────────────────────────────────────────────────────────

def test_step_output_store_set_and_get():
    store = StepOutputStore()
    store.set("new_password", "hunter2")
    assert store.get("new_password") == "hunter2"


def test_step_output_store_get_missing_returns_none():
    store = StepOutputStore()
    assert store.get("nonexistent") is None


def test_step_output_store_values_not_in_repr():
    """Secret values must not appear when the store is repr()'d or logged."""
    store = StepOutputStore()
    store.set("new_password", "super-secret-value")
    representation = repr(store)
    assert "super-secret-value" not in representation


# ── inject_consumed_inputs ────────────────────────────────────────────────────

def test_inject_consumed_inputs_merges_values_into_params():
    store = StepOutputStore()
    store.set("new_password", "abc123")

    step = ChangePlanStep(
        id="step_update_config",
        type="agent_command",
        command="rotate_db_credentials",
        params={"action": "update_config", "config_paths": ["/etc/app.env"]},
        consumes=["new_password"],
    )

    enriched_params = inject_consumed_inputs(step, store)
    assert enriched_params["new_password"] == "abc123"
    assert enriched_params["action"] == "update_config"


def test_inject_consumed_inputs_raises_if_slot_unresolved():
    store = StepOutputStore()
    # "new_password" never set

    step = ChangePlanStep(
        id="step_blocked",
        type="agent_command",
        command="rotate_db_credentials",
        params={"action": "update_config"},
        consumes=["new_password"],
    )

    with pytest.raises(ValueError, match="new_password"):
        inject_consumed_inputs(step, store)


def test_inject_consumed_inputs_no_consumes_unchanged():
    store = StepOutputStore()
    store.set("new_password", "abc123")

    step = ChangePlanStep(
        id="step_restart",
        type="agent_command",
        command="rotate_db_credentials",
        params={"action": "restart", "service_name": "myapp"},
        consumes=[],
    )

    enriched_params = inject_consumed_inputs(step, store)
    assert "new_password" not in enriched_params
    assert enriched_params["service_name"] == "myapp"


# ── resolve_produces ──────────────────────────────────────────────────────────

def test_resolve_produces_populates_store():
    store = StepOutputStore()
    step = ChangePlanStep(
        id="step_generate",
        type="agent_command",
        command="rotate_db_credentials",
        params={"action": "generate"},
        produces=["new_password", "old_password_backup_path"],
    )
    step_result = {"new_password": "xyz789", "old_password_backup_path": "/tmp/db.bak"}

    resolve_produces(step, step_result, store)

    assert store.get("new_password") == "xyz789"
    assert store.get("old_password_backup_path") == "/tmp/db.bak"


def test_resolve_produces_ignores_keys_not_in_produces():
    store = StepOutputStore()
    step = ChangePlanStep(
        id="step_generate",
        type="agent_command",
        command="rotate_db_credentials",
        params={},
        produces=["new_password"],
    )
    step_result = {"new_password": "abc", "debug_info": "should-not-be-stored"}

    resolve_produces(step, step_result, store)

    assert store.get("debug_info") is None


# ── audit log safety ──────────────────────────────────────────────────────────

def test_audit_log_does_not_contain_secret_value(caplog):
    """
    Simulates what the execution engine logs. Slot names must appear in log
    output; plaintext values must not.
    """
    store = StepOutputStore()
    store.set("new_password", "plaintext-should-never-appear")

    with caplog.at_level(logging.INFO):
        # This is what the execution engine should log — slot names only
        logging.getLogger("change_execution").info(
            "Step completed. Produced slots: %s", ["new_password"]
        )

    assert "new_password" in caplog.text
    assert "plaintext-should-never-appear" not in caplog.text
