# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Workflow runner abstraction.

Mirrors Temporal's Workflow/Activity pattern:
  - Workflows are pure, deterministic orchestration logic
  - Activities are the external calls (DB writes, connector calls)
  - WorkflowRunner.start_workflow() returns a workflow_id and runs async

This can be swapped for a real Temporal worker by implementing the same
interface against temporalio.client.Client and temporalio.worker.Worker.
"""
import asyncio
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Coroutine
import logging

logger = logging.getLogger(__name__)

_running_workflows: dict[str, asyncio.Task] = {}


@dataclass
class WorkflowInput:
    """Serializable input for ExecuteChangeWorkflow. Add fields without breaking existing runs."""
    change_request_id: str
    organization_id: str
    initiator_id: str


@dataclass
class WorkflowResult:
    workflow_id: str
    status: str
    result: dict[str, Any]


async def start_workflow(
    workflow_fn: Callable[..., Coroutine],
    input: WorkflowInput,
    workflow_id: str | None = None,
) -> str:
    wf_id = workflow_id or f"wf-{uuid.uuid4()}"
    task = asyncio.create_task(
        _run_with_error_capture(wf_id, workflow_fn, input),
        name=wf_id,
    )
    _running_workflows[wf_id] = task
    task.add_done_callback(lambda t: _running_workflows.pop(wf_id, None))
    return wf_id


async def _run_with_error_capture(
    wf_id: str,
    workflow_fn: Callable,
    input: WorkflowInput,
) -> None:
    try:
        await workflow_fn(input)
    except Exception as exc:
        logger.error("Workflow %s failed with unhandled exception: %s", wf_id, exc, exc_info=True)


def get_workflow_status(workflow_id: str) -> str:
    task = _running_workflows.get(workflow_id)
    if task is None:
        return "not_found"
    if task.done():
        return "completed" if not task.exception() else "failed"
    return "running"
