# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Change execution engine — step-output propagation.

StepOutputStore holds in-memory slot values for the lifetime of a single
change execution. Values are NEVER written to the database or logged.
Only slot names appear in log output.
"""

import logging
from typing import Any
from app.models.change_plan import ChangePlanStep

log = logging.getLogger("change_execution")


class StepOutputStore:
    """
    In-memory map of slot_name → plaintext_value for a single change execution.

    __repr__ deliberately omits values to prevent accidental log exposure.
    """

    def __init__(self):
        self._store: dict[str, str] = {}

    def set(self, slot: str, value: str) -> None:
        self._store[slot] = value

    def get(self, slot: str) -> str | None:
        return self._store.get(slot)

    def __repr__(self) -> str:
        # Intentionally omit values — only show slot names
        return f"StepOutputStore(slots={list(self._store.keys())})"


def inject_consumed_inputs(step: ChangePlanStep, store: StepOutputStore) -> dict[str, Any]:
    """
    Returns a copy of step.params enriched with values resolved from store
    for every slot listed in step.consumes.

    Raises ValueError if any consumed slot has no resolved value (prior step
    failed to produce it). The change engine must abort the step on this error.
    """
    params = dict(step.params)
    for slot in step.consumes:
        value = store.get(slot)
        if value is None:
            raise ValueError(
                f"Step '{step.id}' consumes slot '{slot}' but no prior step produced it. "
                "Check that the producing step completed successfully."
            )
        params[slot] = value
    return params


def resolve_produces(
    step: ChangePlanStep,
    step_result: dict[str, Any],
    store: StepOutputStore,
) -> None:
    """
    After a step succeeds, reads each slot listed in step.produces from
    step_result and writes it into store.

    Only slots declared in step.produces are stored — all other step_result
    keys are ignored. This prevents accidental credential leakage from steps
    that return extra debug data.

    Logs slot names only, never values.
    """
    for slot in step.produces:
        if slot in step_result:
            store.set(slot, step_result[slot])
            log.info("Step '%s' produced slot '%s'", step.id, slot)
        else:
            log.warning(
                "Step '%s' declared produces=['%s'] but result had no key '%s'",
                step.id, slot, slot,
            )
