"""
Nexplane MCP tools — Change Requests domain (10 tools).

All infrastructure-touching write tools produce CRs in draft state.
The caller must separately approve and execute.
"""
from __future__ import annotations
import uuid as _uuid
from typing import Any, Optional

from app.mcp_server import mcp
from app.database import AsyncSessionLocal


async def _auth(token: str):
    from app.mcp_server import resolve_mcp_token
    from fastapi import HTTPException
    db_cm = AsyncSessionLocal()
    db = await db_cm.__aenter__()
    try:
        user = await resolve_mcp_token(token, db)
        return user, db, db_cm
    except HTTPException:
        await db_cm.__aexit__(None, None, None)
        raise


@mcp.tool()
async def list_change_types(token: str) -> list[dict[str, Any]]:
    """
    List all available Change Request types with their display names and descriptions.
    Use this to discover what CRs can be created before calling create_change_request.
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
    Get the full schema for a specific Change Request type including all parameters and their types.
    Use this to understand what parameters are required before creating a CR.
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
    List Change Requests for the org. Filter by status (draft/approved/executed/rolled_back/failed),
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
    Automatically includes the asset context bundle so you can validate the plan is appropriate
    before approving. Call this after create_change_request and before approve_change_request.
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
    parameters: dict[str, Any],
) -> dict[str, Any]:
    """
    Create a draft Change Request for a specific change_type against a target asset.
    The CR is created in draft state — it must be approved and executed separately.
    This tool NEVER executes a change directly. Returns the draft CR and asset context bundle
    so you can validate the plan before approving. Use list_change_types to discover available types.
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
    Approve a Change Request. Respects the authenticated user's role — tokens without approval
    permission will be rejected. A user cannot approve a CR they created (platform-enforced).
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
        if cr.status != ChangeRequestStatus.draft:
            return {"error": f"CR is in {cr.status} state — only draft CRs can be approved"}

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
    Execute an approved Change Request. This triggers the executor against the target connector.
    The CR must be in approved state. Execution is asynchronous — poll get_change_request for status.
    """
    from sqlalchemy import select
    from app.models.change_request import ChangeRequest, ChangeRequestStatus
    from app.workflows.execute_change_workflow import execute_change_workflow

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

        cr.status = ChangeRequestStatus.executing
        await db.commit()

        import asyncio
        asyncio.create_task(execute_change_workflow(str(cr.id), AsyncSessionLocal))
        return {"id": str(cr.id), "status": "executing", "message": "Execution started; poll get_change_request for status updates"}
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def rollback_change_request(token: str, cr_id: str) -> dict[str, Any]:
    """
    Roll back an executed Change Request using the stored rollback snapshot.
    Only executed CRs can be rolled back. Creates a rollback execution run.
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
        if cr.status != ChangeRequestStatus.executed:
            return {"error": f"Only executed CRs can be rolled back; current status: {cr.status}"}

        from app.workflows import runner as workflow_runner
        import asyncio
        asyncio.create_task(workflow_runner.rollback(str(cr.id), AsyncSessionLocal))
        return {"id": str(cr.id), "status": "rolling_back", "message": "Rollback started; poll get_change_request for status updates"}
    finally:
        await db_cm.__aexit__(None, None, None)
