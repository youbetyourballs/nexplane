# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Unified execution entry point for all CR execution sources.

All paths — manual, scheduled, recurring, AI-assisted — must go through
ChangeExecutionService.start() so approval checks, audit logging, and
workflow invocation stay consistent.
"""
import uuid
import uuid as _uuid_mod
import logging

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.change_request import ChangeRequest, ChangeRequestStatus
from app.models.execution_run import ExecutionRun, ExecutionStatus
from app.services.audit_service import record_event
from app.workflows import runner as workflow_runner
from app.workflows.execute_change_workflow import execute_change_workflow

logger = logging.getLogger(__name__)

_EXECUTABLE_STATUSES = {ChangeRequestStatus.approved}

VALID_SOURCES = frozenset({
    "manual", "scheduled", "recurring_job", "maintenance_window_release", "api", "ai_assisted"
})


class ChangeExecutionService:
    @staticmethod
    async def start(
        cr_id: uuid.UUID,
        actor_id: uuid.UUID,
        source: str,
        db: AsyncSession,
    ) -> ExecutionRun:
        """Start execution of an approved CR.

        Enforces: status is executable, audit logged, workflow started.
        Raises HTTPException on policy violations.
        """
        cr = await db.get(ChangeRequest, cr_id)
        if not cr:
            raise HTTPException(status_code=404, detail="Change request not found")

        if cr.status not in _EXECUTABLE_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot execute CR in status '{cr.status.value}'. Must be 'approved'.",
            )

        if source not in VALID_SOURCES:
            raise HTTPException(status_code=400, detail=f"Unknown execution source: {source!r}")

        workflow_id = f"wf-cr-{cr.id}-{_uuid_mod.uuid4().hex[:8]}"
        run = ExecutionRun(
            change_request_id=cr.id,
            workflow_id=workflow_id,
            status=ExecutionStatus.pending,
        )
        db.add(run)
        await db.flush()

        await record_event(
            db,
            cr.organization_id,
            "execution.initiated",
            {"change_request_id": str(cr.id), "workflow_id": workflow_id, "source": source},
            actor_id=actor_id,
            change_request_id=cr.id,
        )
        await db.commit()

        wf_input = workflow_runner.WorkflowInput(
            change_request_id=str(cr.id),
            organization_id=str(cr.organization_id),
            initiator_id=str(actor_id),
        )
        await workflow_runner.start_workflow(execute_change_workflow, wf_input, workflow_id=workflow_id)

        logger.info("CR %s execution started via source=%s workflow=%s", cr.id, source, workflow_id)
        return run
