"""IR change request executor — parallel and sequential step orchestration."""
import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.change_request import ChangeRequest, ChangeRequestStatus


async def _record_step(
    db: AsyncSession,
    cr: ChangeRequest,
    step_name: str,
    status: str,
    started_at: datetime,
    error: str | None = None,
    state: dict | None = None,
) -> None:
    """Write a single step result into change_requests.step_results via JSONB merge."""
    step_result: dict[str, Any] = {
        "status": status,
        "started_at": started_at.isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    if error:
        step_result["error"] = error
    if state:
        step_result["state"] = state

    # Merge into existing step_results dict
    cr.step_results = {**cr.step_results, step_name: step_result}
    await db.flush()


async def _run_step(
    db: AsyncSession,
    cr: ChangeRequest,
    step_name: str,
    coro,
) -> bool:
    """Run a step coroutine, record result, return True on success."""
    started = datetime.now(timezone.utc)
    try:
        result = await coro
        await _record_step(db, cr, step_name, "completed", started, state=result)
        return True
    except Exception as exc:
        # Retry once after 10 seconds for connector steps
        await asyncio.sleep(10)
        try:
            result = await coro
            await _record_step(db, cr, step_name, "completed", started, state=result)
            return True
        except Exception as exc2:
            await _record_step(db, cr, step_name, "failed", started, error=str(exc2))
            return False


async def execute_ir_change_request(cr_id: uuid.UUID, db: AsyncSession) -> None:
    """
    Loads the IR change request, dispatches steps per the playbook type
    (parallel via asyncio.gather or sequential), records per-step results,
    and sets the final status on the change request.
    """
    result = await db.execute(select(ChangeRequest).where(ChangeRequest.id == cr_id))
    cr = result.scalar_one_or_none()
    if cr is None or not cr.incident_response:
        return

    cr.status = ChangeRequestStatus.executing
    await db.flush()

    playbook_type = cr.ir_playbook_type
    params = cr.desired_outcome  # parameters stored in desired_outcome JSONB

    all_ok = True

    if playbook_type == "isolate_host":
        all_ok = await _execute_isolate_host(db, cr, params)
    elif playbook_type == "lockdown_account":
        all_ok = await _execute_lockdown_account(db, cr, params)
    elif playbook_type == "phishing_response":
        all_ok = await _execute_phishing_response(db, cr, params)
    elif playbook_type == "preserve_evidence":
        all_ok = await _execute_preserve_evidence(db, cr, params)
    else:
        cr.status = ChangeRequestStatus.failed
        cr.output = {"error": f"Unknown IR playbook type: {playbook_type}"}
        await db.flush()
        return

    cr.status = ChangeRequestStatus.completed if all_ok else ChangeRequestStatus.failed
    await db.flush()


async def _execute_isolate_host(db: AsyncSession, cr: ChangeRequest, params: dict) -> bool:
    """Sequential: optional preserve_evidence -> isolate."""
    # Step 1: preserve_evidence (unless skipped or recent bundle exists)
    if not params.get("skip_evidence", False):
        ok = await _run_step(db, cr, "preserve_evidence",
                             _noop_placeholder("preserve_evidence"))
        if not ok:
            return False

    # Step 2: isolate via agent command
    ok = await _run_step(db, cr, "isolate",
                         _noop_placeholder("isolate_host_agent_command"))
    return ok


async def _execute_lockdown_account(db: AsyncSession, cr: ChangeRequest, params: dict) -> bool:
    """All connector steps run in parallel."""
    step_names = ["ad_disable", "okta_suspend", "entraid_suspend",
                  "google_suspend", "github_revoke", "slack_deactivate"]
    coros = [_noop_placeholder(name) for name in step_names]
    started = datetime.now(timezone.utc)
    results = await asyncio.gather(*coros, return_exceptions=True)

    all_ok = True
    for name, result in zip(step_names, results):
        if isinstance(result, Exception):
            await _record_step(db, cr, name, "failed", started, error=str(result))
            all_ok = False
        else:
            await _record_step(db, cr, name, "completed", started, state=result)

    return all_ok


async def _execute_phishing_response(db: AsyncSession, cr: ChangeRequest, params: dict) -> bool:
    """Five sequential phases; within each phase steps run in parallel."""
    # Phase 1
    phase1 = ["defender_block_domain", "google_block_domain"]
    results = await asyncio.gather(*[_noop_placeholder(n) for n in phase1], return_exceptions=True)
    started = datetime.now(timezone.utc)
    all_ok = True
    for name, r in zip(phase1, results):
        if isinstance(r, Exception):
            await _record_step(db, cr, name, "failed", started, error=str(r))
            all_ok = False
        else:
            await _record_step(db, cr, name, "completed", started, state=r)

    # Phases 2-4 per user — simplified: run all per-user steps in parallel
    affected = params.get("affected_user_emails", [])
    for email in affected:
        per_user_steps = [
            f"{email}:ad_force_password_reset", f"{email}:okta_expire_password",
            f"{email}:google_force_password_change", f"{email}:entraid_force_password_change",
            f"{email}:ad_revoke_kerberos", f"{email}:okta_revoke_sessions",
            f"{email}:google_revoke_tokens", f"{email}:okta_reset_factors",
            f"{email}:entraid_reset_mfa", f"{email}:google_reset_2sv",
        ]
        step_coros = [_noop_placeholder(s) for s in per_user_steps]
        step_results = await asyncio.gather(*step_coros, return_exceptions=True)
        ts = datetime.now(timezone.utc)
        for name, r in zip(per_user_steps, step_results):
            if isinstance(r, Exception):
                await _record_step(db, cr, name, "failed", ts, error=str(r))
                all_ok = False
            else:
                await _record_step(db, cr, name, "completed", ts, state=r)

    # Phase 5: report
    await _record_step(db, cr, "aggregate_report", "completed", datetime.now(timezone.utc))
    cr.output = {
        "sender_domain": params.get("sender_domain"),
        "affected_users": affected,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    return all_ok


async def _execute_preserve_evidence(db: AsyncSession, cr: ChangeRequest, params: dict) -> bool:
    """Sequential: generate URL -> agent collect -> store manifest."""
    ok = await _run_step(db, cr, "collect_forensics",
                         _noop_placeholder("collect_forensics_agent_command"))
    return ok


async def _noop_placeholder(name: str) -> dict:
    """
    Placeholder for actual connector/agent dispatch.
    Replace with real connector call or agent command dispatch in follow-on work.
    """
    await asyncio.sleep(0)
    return {"step": name, "status": "ok"}
