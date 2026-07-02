# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Nexplane MCP tools — Change Requests domain (10 tools).

All infrastructure-touching write tools produce CRs in draft state.
The caller must separately approve and execute.
"""
import logging
import uuid as _uuid
from typing import Any, Optional

from app.mcp_server import mcp
from app.database import AsyncSessionLocal

logger = logging.getLogger(__name__)


async def _auth(token: str):
    from app.mcp_server import resolve_mcp_token
    from fastapi import HTTPException
    db_cm = AsyncSessionLocal()
    db = await db_cm.__aenter__()
    try:
        user, agent_token = await resolve_mcp_token(token, db)
        return user, db, db_cm
    except HTTPException:
        await db_cm.__aexit__(None, None, None)
        raise


@mcp.tool()
async def list_change_types(token: str) -> list[dict[str, Any]]:
    """
    Discover what infrastructure changes are available. Use this first to find the right
    change_type before creating a CR.
    """
    from app.connectors.catalog_service import get_catalog
    user, db, db_cm = await _auth(token)
    try:
        catalog = get_catalog()
        return [
            {
                "change_type": ct.change_type,
                "display_name": ct.display_name,
                "description": ct.description,
            }
            for ct in catalog.values()
        ]
    except Exception:
        return []
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def get_change_type(token: str, change_type: str) -> dict[str, Any]:
    """
    Get the full parameter schema for a change type including all parameters and their types.
    Use to understand what's required before creating a CR.
    """
    from app.connectors.catalog_service import get_catalog
    user, db, db_cm = await _auth(token)
    try:
        catalog = get_catalog()
        ct = catalog.get(change_type)
        if ct is None:
            return {"error": f"Unknown change_type: {change_type}"}
        return {
            "change_type": ct.change_type,
            "display_name": ct.display_name,
            "description": ct.description,
            "parameters": ct.parameters if hasattr(ct, "parameters") else {},
            "steps": ct.steps if hasattr(ct, "steps") else [],
            "preflight_checks": ct.preflight_checks if hasattr(ct, "preflight_checks") else [],
        }
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def list_change_requests(
    token: str,
    status: str = None,
    change_type: str = None,
    asset_id: str = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    Query change history for the org. Use to answer: what changed recently, who approved it,
    and what's in flight? Filter by status (draft/approved/executed/rolled_back/failed),
    change_type, or asset_id. Returns summary fields — use get_change_request for full detail.
    """
    from sqlalchemy import select
    from app.models.change_request import ChangeRequest

    user, db, db_cm = await _auth(token)
    try:
        stmt = select(ChangeRequest).where(
            ChangeRequest.organization_id == user.organization_id
        ).order_by(ChangeRequest.created_at.desc()).limit(limit)

        if status:
            stmt = stmt.where(ChangeRequest.status == status)
        if change_type:
            stmt = stmt.where(ChangeRequest.change_type == change_type)

        result = await db.execute(stmt)
        crs = result.scalars().all()

        filtered = crs
        if asset_id:
            filtered = [cr for cr in crs if asset_id in (cr.target_asset_ids or [])]

        return [
            {
                "id": str(cr.id),
                "title": cr.title,
                "change_type": str(cr.change_type),
                "status": str(cr.status),
                "target_asset_ids": cr.target_asset_ids,
                "created_at": cr.created_at.isoformat() if cr.created_at else None,
            }
            for cr in filtered
        ]
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def get_change_request(token: str, cr_id: str) -> dict[str, Any]:
    """
    Get full CR detail including plan steps, parameters, approval history, and execution log.
    Automatically includes the asset context bundle so you can validate the plan is appropriate.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from app.models.change_request import ChangeRequest
    from app.models.approval import Approval
    from app.mcp_tools.context import build_asset_context

    user, db, db_cm = await _auth(token)
    try:
        result = await db.execute(
            select(ChangeRequest).where(
                ChangeRequest.id == _uuid.UUID(cr_id),
                ChangeRequest.organization_id == user.organization_id,
            ).options(selectinload(ChangeRequest.approvals))
        )
        cr = result.scalar_one_or_none()
        if cr is None:
            return {"error": "Change request not found"}

        asset_ids = cr.target_asset_ids or []
        ctx = {}
        if asset_ids:
            try:
                ctx = await build_asset_context(_uuid.UUID(asset_ids[0]), db)
            except Exception:
                pass

        approvals = [
            {
                "approver_id": str(a.approver_id),
                "decision": str(a.decision),
                "decided_at": a.created_at.isoformat() if a.created_at else None,
                "comment": a.comment,
            }
            for a in (cr.approvals or [])
        ]
        return {
            "id": str(cr.id),
            "title": cr.title,
            "change_type": str(cr.change_type),
            "status": str(cr.status),
            "desired_outcome": cr.desired_outcome,
            "target_asset_ids": asset_ids,
            "created_at": cr.created_at.isoformat() if cr.created_at else None,
            "approvals": approvals,
            "asset_context": ctx,
        }
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def get_change_request_plan(token: str, cr_id: str) -> dict[str, Any]:
    """
    Get the AI-generated execution plan for a CR — steps, estimated impact, rollback path.
    Review before approving. Call this after create_change_request and before approve_change_request.
    Automatically includes the asset context bundle.
    """
    from sqlalchemy import select
    from app.models.change_request import ChangeRequest
    from app.models.change_plan import ChangePlan
    from app.mcp_tools.context import build_asset_context

    user, db, db_cm = await _auth(token)
    try:
        result = await db.execute(
            select(ChangeRequest).where(
                ChangeRequest.id == _uuid.UUID(cr_id),
                ChangeRequest.organization_id == user.organization_id,
            )
        )
        cr = result.scalar_one_or_none()
        if cr is None:
            return {"error": "Change request not found"}

        plan_result = await db.execute(
            select(ChangePlan).where(ChangePlan.change_request_id == cr.id)
            .order_by(ChangePlan.created_at.desc()).limit(1)
        )
        plan = plan_result.scalar_one_or_none()
        asset_ids = cr.target_asset_ids or []
        ctx = {}
        if asset_ids:
            try:
                ctx = await build_asset_context(_uuid.UUID(asset_ids[0]), db)
            except Exception:
                pass

        return {
            "cr_id": str(cr.id),
            "plan": plan.plan_data if plan else None,
            "generated_by": str(plan.generated_by) if plan else None,
            "asset_context": ctx,
        }
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def create_change_request(
    token: str,
    change_type: str,
    asset_id: str,
    title: str,
    parameters: dict,
) -> dict[str, Any]:
    """
    Create a draft Change Request for an infrastructure change against a target asset.
    The CR is created in draft state — it must be reviewed, approved, and executed separately.
    This tool NEVER executes a change directly. Use list_change_types to discover available types.
    Returns the draft CR and asset context bundle so you can validate the plan before approving.
    """
    from app.models.change_request import ChangeRequest, ChangeRequestStatus, ChangeType
    from app.mcp_tools.context import build_asset_context

    user, db, db_cm = await _auth(token)
    try:
        cr = ChangeRequest(
            organization_id=user.organization_id,
            requester_id=user.id,
            change_type=change_type,
            target_asset_ids=[asset_id],
            title=title,
            status=ChangeRequestStatus.draft,
            desired_outcome=parameters,
        )
        db.add(cr)
        await db.flush()
        await db.commit()
        await db.refresh(cr)
        ctx = {}
        try:
            ctx = await build_asset_context(_uuid.UUID(asset_id), db)
        except Exception:
            pass
        return {
            "id": str(cr.id),
            "title": cr.title,
            "change_type": cr.change_type.value if hasattr(cr.change_type, 'value') else str(cr.change_type),
            "status": cr.status.value if hasattr(cr.status, 'value') else str(cr.status),
            "asset_id": asset_id,
            "created_at": cr.created_at.isoformat() if cr.created_at else None,
            "asset_context": ctx,
        }
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def approve_change_request(token: str, cr_id: str, comment: str = None) -> dict[str, Any]:
    """
    Approve a Change Request. Respects role-based permissions — tokens without approval
    permission are rejected. A user cannot approve a CR they created (platform-enforced).
    """
    from sqlalchemy import select
    from app.models.change_request import ChangeRequest, ChangeRequestStatus
    from app.models.approval import Approval, ApprovalDecision
    from app.models.user import UserRole

    user, db, db_cm = await _auth(token)
    try:
        if user.role not in (UserRole.admin, UserRole.approver):
            return {"error": "Insufficient permissions to approve Change Requests"}

        result = await db.execute(
            select(ChangeRequest).where(
                ChangeRequest.id == _uuid.UUID(cr_id),
                ChangeRequest.organization_id == user.organization_id,
            )
        )
        cr = result.scalar_one_or_none()
        if cr is None:
            return {"error": "Change request not found"}
        if str(cr.requester_id) == str(user.id):
            return {"error": "Cannot approve a Change Request you created"}
        if cr.status not in (ChangeRequestStatus.draft, ChangeRequestStatus.awaiting_approval):
            return {"error": f"CR is in {cr.status} state — only draft or awaiting_approval CRs can be approved"}

        approval = Approval(
            change_request_id=cr.id,
            approver_id=user.id,
            decision=ApprovalDecision.approved,
            comment=comment,
        )
        db.add(approval)
        cr.status = ChangeRequestStatus.approved
        await db.commit()
        return {"id": str(cr.id), "status": str(cr.status)}
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def reject_change_request(token: str, cr_id: str, reason: str) -> dict[str, Any]:
    """Reject a Change Request with a stated reason."""
    from sqlalchemy import select
    from app.models.change_request import ChangeRequest, ChangeRequestStatus
    from app.models.approval import Approval, ApprovalDecision
    from app.models.user import UserRole

    user, db, db_cm = await _auth(token)
    try:
        if user.role not in (UserRole.admin, UserRole.approver):
            return {"error": "Insufficient permissions to reject Change Requests"}

        result = await db.execute(
            select(ChangeRequest).where(
                ChangeRequest.id == _uuid.UUID(cr_id),
                ChangeRequest.organization_id == user.organization_id,
            )
        )
        cr = result.scalar_one_or_none()
        if cr is None:
            return {"error": "Change request not found"}

        approval = Approval(
            change_request_id=cr.id,
            approver_id=user.id,
            decision=ApprovalDecision.rejected,
            comment=reason,
        )
        db.add(approval)
        cr.status = ChangeRequestStatus.rejected
        await db.commit()
        return {"id": str(cr.id), "status": str(cr.status)}
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def execute_change_request(token: str, cr_id: str) -> dict[str, Any]:
    """
    Execute an approved Change Request. Triggers the executor against the target connector.
    The CR must be in approved state. Execution is asynchronous — poll get_change_request for status.
    """
    from sqlalchemy import select
    from app.models.change_request import ChangeRequest, ChangeRequestStatus
    from app.services.change_execution_service import ChangeExecutionService

    user, db, db_cm = await _auth(token)
    try:
        result = await db.execute(
            select(ChangeRequest).where(
                ChangeRequest.id == _uuid.UUID(cr_id),
                ChangeRequest.organization_id == user.organization_id,
            )
        )
        cr = result.scalar_one_or_none()
        if cr is None:
            return {"error": "Change request not found"}
        if cr.status != ChangeRequestStatus.approved:
            return {"error": f"CR must be approved before execution; current status: {cr.status}"}

        # Delegate to the same service used by the REST router so workflow
        # lifecycle (preflight, execution, audit events) is consistent.
        try:
            await ChangeExecutionService.start(
                cr_id=cr.id,
                actor_id=user.id,
                source="api",
                db=db,
            )
        except ValueError as exc:
            # Already in executing state — treat as informational
            return {"id": str(cr.id), "status": "executing", "note": str(exc)}
        except Exception as exc:
            logger.exception("execute_change_request: start failed for CR %s", cr.id)
            return {"error": f"Failed to start execution: {exc}"}

        return {"id": str(cr.id), "status": "executing", "message": "Execution started; poll get_execution_progress for status updates"}
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def rollback_change_request(token: str, cr_id: str) -> dict[str, Any]:
    """
    Roll back an executed Change Request using the stored rollback snapshot. Only executed CRs
    can be rolled back. Creates a rollback execution run. Returns rollback feasibility and steps.
    """
    from sqlalchemy import select
    from app.models.change_request import ChangeRequest, ChangeRequestStatus

    user, db, db_cm = await _auth(token)
    try:
        result = await db.execute(
            select(ChangeRequest).where(
                ChangeRequest.id == _uuid.UUID(cr_id),
                ChangeRequest.organization_id == user.organization_id,
            )
        )
        cr = result.scalar_one_or_none()
        if cr is None:
            return {"error": "Change request not found"}
        rollbackable = (
            ChangeRequestStatus.completed,
            ChangeRequestStatus.failed,
        )
        if cr.status not in rollbackable:
            return {"error": f"Only completed or failed CRs can be rolled back; current status: {cr.status}"}

        from app.services.rollback_executor import execute_cr_rollback

        # Await the rollback directly so MCP callers get a synchronous result.
        # This is safe because MCP tool calls are themselves async.
        try:
            result_data = await execute_cr_rollback(cr.id)
            rb_status = "rolled_back"
        except Exception as exc:
            logger.error("MCP rollback failed for CR %s: %s", cr.id, exc)
            result_data = {"error": str(exc)}
            rb_status = "rollback_failed"

        return {"id": str(cr.id), "status": rb_status, "result": result_data}
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def submit_for_approval(token: str, cr_id: str) -> dict:
    """
    Move a CR from Draft to Awaiting Approval so approvers are notified.
    Use after create_change_request and reviewing get_change_request_plan.
    Returns updated CR status.
    """
    from sqlalchemy import select
    from app.models.change_request import ChangeRequest, ChangeRequestStatus

    user, db, db_cm = await _auth(token)
    try:
        result = await db.execute(
            select(ChangeRequest).where(
                ChangeRequest.id == _uuid.UUID(cr_id),
                ChangeRequest.organization_id == user.organization_id,
            )
        )
        cr = result.scalar_one_or_none()
        if cr is None:
            return {"error": "Change request not found"}
        if cr.status != ChangeRequestStatus.draft:
            return {"error": f"CR must be in draft state to submit for approval; current status: {cr.status}"}

        cr.status = ChangeRequestStatus.awaiting_approval
        await db.commit()
        return {
            "id": str(cr.id),
            "status": cr.status.value if hasattr(cr.status, "value") else str(cr.status),
            "message": "CR submitted for approval; approvers have been notified.",
        }
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def get_execution_progress(token: str, cr_id: str) -> dict:
    """
    Poll execution progress for a CR currently in Executing state.
    Returns completed_steps, total_steps, current_step, percent_complete, and any error messages.
    Call repeatedly until status is 'completed' or 'failed'.
    """
    from sqlalchemy import select
    from app.models.change_request import ChangeRequest

    from sqlalchemy.orm import selectinload
    from app.models.execution_run import ExecutionRun, ExecutionStatus

    user, db, db_cm = await _auth(token)
    try:
        result = await db.execute(
            select(ChangeRequest).where(
                ChangeRequest.id == _uuid.UUID(cr_id),
                ChangeRequest.organization_id == user.organization_id,
            )
        )
        cr = result.scalar_one_or_none()
        if cr is None:
            return {"error": "Change request not found"}

        # Get the most recent execution run for progress data
        runs_result = await db.execute(
            select(ExecutionRun).where(
                ExecutionRun.change_request_id == cr.id,
            ).order_by(ExecutionRun.started_at.desc()).limit(1)
        )
        latest_run = runs_result.scalar_one_or_none()

        exec_result = {}
        if latest_run and latest_run.result:
            exec_result = latest_run.result if isinstance(latest_run.result, dict) else {}

        completed = exec_result.get("completed_steps", 0)
        total = exec_result.get("total_steps", 0)
        percent = int(completed / total * 100) if total else 0
        current_step = exec_result.get("current_step", None)
        errors = exec_result.get("errors", [])

        cr_status = cr.status.value if hasattr(cr.status, "value") else str(cr.status)
        # Normalize status for poll logic
        if cr_status == "completed":
            percent = 100

        return {
            "cr_id": str(cr.id),
            "status": cr_status,
            "completed_steps": completed,
            "total_steps": total,
            "current_step": current_step,
            "percent_complete": percent,
            "errors": errors,
        }
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def get_cr_manifest(
    token: str,
    domain: Optional[str] = None,
    action_class: Optional[str] = None,
    touches: Optional[str] = None,
    rollback_type: Optional[str] = None,
) -> dict[str, Any]:
    """
    Return the full CR type vocabulary enriched with planning metadata.

    Each entry includes: change_type, display_name, domain, action_class, touches,
    preconditions, effects, rollback_type, and parameters.

    Use filters to load only the relevant slice for a planning goal:
    - domain: hardening | credential_rotation | incident_response | compliance |
               identity | aws_compute | aws_identity | gcp | azure | oci |
               iac | backup_dr | networking | infrastructure
    - action_class: create | delete | configure | rotate | patch | scan |
                    audit | isolate | observe | verify | execute
    - touches: ec2_instance | linux_host | iam_user | selinux_policy | ...
    - rollback_type: reversible | permanent | snapshot_based

    Returns {"count": N, "entries": [...]}
    """
    from app.services.manifest_builder import get_manifest
    user, db, db_cm = await _auth(token)
    try:
        entries = get_manifest(
            domain=domain,
            action_class=action_class,
            touches=touches,
            rollback_type=rollback_type,
        )
        return {"count": len(entries), "entries": entries}
    except Exception as exc:
        return {"error": str(exc)}
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def explain_change_request(token: str, cr_id: str) -> dict[str, Any]:
    """
    Get a compact human-readable summary of a CR suitable for LLM reasoning: what it does,
    what it touches, the risk level, who approved it, and whether rollback is available.
    Use instead of get_change_request when you need a quick, structured overview.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from app.models.change_request import ChangeRequest
    from app.models.approval import Approval
    from app.models.change_plan import ChangePlan

    user, db, db_cm = await _auth(token)
    try:
        result = await db.execute(
            select(ChangeRequest).where(
                ChangeRequest.id == _uuid.UUID(cr_id),
                ChangeRequest.organization_id == user.organization_id,
            ).options(selectinload(ChangeRequest.approvals))
        )
        cr = result.scalar_one_or_none()
        if cr is None:
            return {"error": "Change request not found"}

        # Fetch latest plan for risk info
        plan_result = await db.execute(
            select(ChangePlan).where(ChangePlan.change_request_id == cr.id)
            .order_by(ChangePlan.created_at.desc()).limit(1)
        )
        plan = plan_result.scalar_one_or_none()
        plan_data = plan.plan_data if plan else {}

        approvers = [
            {
                "approver_id": str(a.approver_id),
                "decision": str(a.decision),
                "comment": a.comment,
                "decided_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in (cr.approvals or [])
            if str(a.decision) == "approved"
        ]

        blast = plan_data.get("blast_radius", {})
        rollback = plan_data.get("rollback_plan", {})

        return {
            "id": str(cr.id),
            "title": cr.title,
            "change_type": str(cr.change_type),
            "lifecycle_stage": str(cr.status),
            "description": cr.description,
            "affected_asset_ids": cr.target_asset_ids or [],
            "risk_level": blast.get("estimated_impact", "unknown"),
            "rollback_available": rollback.get("automatic", False),
            "rollback_strategy": rollback.get("strategy", "unknown"),
            "approved_by": approvers,
            "created_at": cr.created_at.isoformat() if cr.created_at else None,
        }
    finally:
        await db_cm.__aexit__(None, None, None)
