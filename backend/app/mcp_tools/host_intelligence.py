# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Nexplane MCP tools — Host Intelligence (16 tools).

Each tool is read-only. Results are cached 300 s per (org, asset, tool).
No approval gate.
"""
from __future__ import annotations

import uuid as _uuid
from typing import Any

from app.database import AsyncSessionLocal
from app.mcp_server import mcp


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


async def _assert_asset_owned(db, user, asset_id: str) -> None:
    from sqlalchemy import select
    from app.models.asset import Asset

    result = await db.execute(
        select(Asset).where(
            Asset.id == _uuid.UUID(asset_id),
            Asset.organization_id == user.organization_id,
        )
    )
    if result.scalar_one_or_none() is None:
        raise ValueError(f"Asset {asset_id} not found or not in your organization")


@mcp.tool()
async def get_kernel_info(token: str, asset_id: str) -> dict[str, Any]:
    """
    Return kernel version, architecture, EOL status, and support state for a host.

    Dispatches a deep_discover agent job and extracts kernel fields from the result.
    Results are cached 300 s — use to answer: is this host running an EOL kernel?
    """
    from app.services.host_intelligence_service import run_intelligence_tool

    user, db, db_cm = await _auth(token)
    try:
        await _assert_asset_owned(db, user, asset_id)
        raw = await run_intelligence_tool(
            db=db, user=user, asset_id=asset_id,
            tool_name="get_kernel_info", command="deep_discover",
            params={}, timeout=120,
        )
        kernel = raw.get("kernel", {})
        return {
            "version": kernel.get("version"),
            "arch": kernel.get("arch"),
            "eol_date": kernel.get("eol_date"),
            "supported": kernel.get("supported"),
        }
    except ValueError as e:
        return {"error": str(e)}
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def get_running_processes(token: str, asset_id: str) -> list[dict[str, Any]]:
    """
    List all running processes on a host including pid, name, owner, command line,
    and open ports per process.
    """
    from app.services.host_intelligence_service import run_intelligence_tool

    user, db, db_cm = await _auth(token)
    try:
        await _assert_asset_owned(db, user, asset_id)
        raw = await run_intelligence_tool(
            db=db, user=user, asset_id=asset_id,
            tool_name="get_running_processes", command="deep_discover",
            params={}, timeout=120,
        )
        procs = raw.get("processes", [])
        return [
            {"pid": p.get("pid"), "name": p.get("name"), "user": p.get("user"),
             "cmdline": p.get("cmdline"), "open_ports": p.get("open_ports", [])}
            for p in procs
        ]
    except ValueError as e:
        return [{"error": str(e)}]
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def get_cron_jobs(token: str, asset_id: str) -> list[dict[str, Any]]:
    """
    List all scheduled cron jobs on a host (crontab, cron.d, systemd timers).
    Returns schedule expression, command, owning user, and source file.
    """
    from app.services.host_intelligence_service import run_intelligence_tool

    user, db, db_cm = await _auth(token)
    try:
        await _assert_asset_owned(db, user, asset_id)
        raw = await run_intelligence_tool(
            db=db, user=user, asset_id=asset_id,
            tool_name="get_cron_jobs", command="audit_scheduled_tasks",
            params={}, timeout=120,
        )
        jobs = raw.get("scheduled_tasks", [])
        return [
            {"schedule": j.get("schedule"), "command": j.get("command"),
             "owner_user": j.get("owner_user"), "source": j.get("source")}
            for j in jobs
        ]
    except ValueError as e:
        return [{"error": str(e)}]
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def get_local_users(token: str, asset_id: str) -> list[dict[str, Any]]:
    """
    List all local user accounts on a host.
    Returns username, uid, gid, group memberships, shell, last login, and locked status.
    """
    from app.services.host_intelligence_service import run_intelligence_tool

    user, db, db_cm = await _auth(token)
    try:
        await _assert_asset_owned(db, user, asset_id)
        raw = await run_intelligence_tool(
            db=db, user=user, asset_id=asset_id,
            tool_name="get_local_users", command="audit_users_and_groups",
            params={}, timeout=120,
        )
        users = raw.get("users", [])
        return [
            {"username": u.get("username"), "uid": u.get("uid"), "gid": u.get("gid"),
             "groups": u.get("groups", []), "shell": u.get("shell"),
             "last_login": u.get("last_login"), "locked": u.get("locked")}
            for u in users
        ]
    except ValueError as e:
        return [{"error": str(e)}]
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def get_installed_packages(token: str, asset_id: str) -> list[dict[str, Any]]:
    """
    List all installed packages on a host (apt, yum/rpm, pip, snap, etc.).
    Returns package name, version, source manager, and install date.
    """
    from app.services.host_intelligence_service import run_intelligence_tool

    user, db, db_cm = await _auth(token)
    try:
        await _assert_asset_owned(db, user, asset_id)
        raw = await run_intelligence_tool(
            db=db, user=user, asset_id=asset_id,
            tool_name="get_installed_packages", command="audit_software_inventory",
            params={}, timeout=120,
        )
        pkgs = raw.get("packages", [])
        return [
            {"name": p.get("name"), "version": p.get("version"),
             "source": p.get("source"), "install_date": p.get("install_date")}
            for p in pkgs
        ]
    except ValueError as e:
        return [{"error": str(e)}]
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def get_running_services(token: str, asset_id: str) -> list[dict[str, Any]]:
    """
    List systemd/init services on a host with their state, enabled flag, and user they run as.
    """
    from app.services.host_intelligence_service import run_intelligence_tool

    user, db, db_cm = await _auth(token)
    try:
        await _assert_asset_owned(db, user, asset_id)
        raw = await run_intelligence_tool(
            db=db, user=user, asset_id=asset_id,
            tool_name="get_running_services", command="deep_discover",
            params={}, timeout=120,
        )
        svcs = raw.get("services", [])
        return [
            {"name": s.get("name"), "state": s.get("state"),
             "enabled": s.get("enabled"), "running_as_user": s.get("running_as_user")}
            for s in svcs
        ]
    except ValueError as e:
        return [{"error": str(e)}]
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def get_open_ports(token: str, asset_id: str) -> list[dict[str, Any]]:
    """
    List all listening TCP/UDP ports on a host with the process that owns each.
    Returns port number, protocol, process name, process owner, and bind address.
    """
    from app.services.host_intelligence_service import run_intelligence_tool

    user, db, db_cm = await _auth(token)
    try:
        await _assert_asset_owned(db, user, asset_id)
        raw = await run_intelligence_tool(
            db=db, user=user, asset_id=asset_id,
            tool_name="get_open_ports", command="deep_discover",
            params={}, timeout=120,
        )
        ports = raw.get("open_ports", [])
        return [
            {"port": p.get("port"), "protocol": p.get("protocol"),
             "process_name": p.get("process_name"), "process_user": p.get("process_user"),
             "listening_on": p.get("listening_on")}
            for p in ports
        ]
    except ValueError as e:
        return [{"error": str(e)}]
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def get_security_posture(token: str, asset_id: str) -> dict[str, Any]:
    """
    Return a summary security posture: SELinux/AppArmor/seccomp modes, firewall rules count,
    and last audit timestamp.
    """
    from app.services.host_intelligence_service import run_intelligence_tool

    user, db, db_cm = await _auth(token)
    try:
        await _assert_asset_owned(db, user, asset_id)
        raw = await run_intelligence_tool(
            db=db, user=user, asset_id=asset_id,
            tool_name="get_security_posture", command="audit_os_security_posture",
            params={}, timeout=120,
        )
        posture = raw.get("posture", raw)
        return {
            "selinux_mode": posture.get("selinux_mode"),
            "apparmor_enabled": posture.get("apparmor_enabled"),
            "seccomp_default": posture.get("seccomp_default"),
            "firewall_rules": posture.get("firewall_rules"),
            "last_audit_at": posture.get("last_audit_at"),
        }
    except ValueError as e:
        return {"error": str(e)}
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def get_seccomp_policy(token: str, asset_id: str) -> dict[str, Any]:
    """
    Return eBPF/seccomp posture: active profiles, per-process profile map, unconfined processes.
    """
    from app.services.host_intelligence_service import run_intelligence_tool

    user, db, db_cm = await _auth(token)
    try:
        await _assert_asset_owned(db, user, asset_id)
        raw = await run_intelligence_tool(
            db=db, user=user, asset_id=asset_id,
            tool_name="get_seccomp_policy", command="audit_ebpf_posture",
            params={}, timeout=120,
        )
        sec = raw.get("seccomp", raw)
        return {
            "active_profiles": sec.get("active_profiles", []),
            "profile_per_process": sec.get("profile_per_process"),
            "unconfined_processes": sec.get("unconfined_processes", []),
        }
    except ValueError as e:
        return {"error": str(e)}
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def get_apparmor_profiles(token: str, asset_id: str) -> list[dict[str, Any]]:
    """
    List AppArmor profiles with their enforcement mode and the processes they confine.
    """
    from app.services.host_intelligence_service import run_intelligence_tool

    user, db, db_cm = await _auth(token)
    try:
        await _assert_asset_owned(db, user, asset_id)
        raw = await run_intelligence_tool(
            db=db, user=user, asset_id=asset_id,
            tool_name="get_apparmor_profiles", command="audit_os_security_posture",
            params={}, timeout=120,
        )
        profiles = raw.get("apparmor_profiles", [])
        return [
            {"profile_name": p.get("profile_name"), "mode": p.get("mode"),
             "confined_processes": p.get("confined_processes", [])}
            for p in profiles
        ]
    except ValueError as e:
        return [{"error": str(e)}]
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def get_selinux_policy(token: str, asset_id: str) -> dict[str, Any]:
    """
    Return SELinux enforcement mode, active policy name, count of recent denials,
    and a sample of the most recent denial messages.
    """
    from app.services.host_intelligence_service import run_intelligence_tool

    user, db, db_cm = await _auth(token)
    try:
        await _assert_asset_owned(db, user, asset_id)
        raw = await run_intelligence_tool(
            db=db, user=user, asset_id=asset_id,
            tool_name="get_selinux_policy", command="audit_os_security_posture",
            params={}, timeout=120,
        )
        se = raw.get("selinux", raw)
        return {
            "mode": se.get("mode"),
            "policy_name": se.get("policy_name"),
            "recent_denials_count": se.get("recent_denials_count"),
            "recent_denials_sample": se.get("recent_denials_sample", []),
        }
    except ValueError as e:
        return {"error": str(e)}
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def get_sudoers(token: str, asset_id: str) -> list[dict[str, Any]]:
    """
    Return all sudoers rules with a risky flag for NOPASSWD or ALL grants.
    """
    from app.services.host_intelligence_service import run_intelligence_tool

    user, db, db_cm = await _auth(token)
    try:
        await _assert_asset_owned(db, user, asset_id)
        raw = await run_intelligence_tool(
            db=db, user=user, asset_id=asset_id,
            tool_name="get_sudoers", command="sudoers_audit",
            params={}, timeout=120,
        )
        rules = raw.get("sudoers_rules", [])
        return [
            {"rule": r.get("rule"), "user": r.get("user"), "risky": r.get("risky")}
            for r in rules
        ]
    except ValueError as e:
        return [{"error": str(e)}]
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def get_authorized_keys(token: str, asset_id: str) -> list[dict[str, Any]]:
    """
    List all SSH authorized_keys entries across all user home directories.
    Returns key fingerprint, owning user, last-used timestamp, and stale flag.
    """
    from app.services.host_intelligence_service import run_intelligence_tool

    user, db, db_cm = await _auth(token)
    try:
        await _assert_asset_owned(db, user, asset_id)
        raw = await run_intelligence_tool(
            db=db, user=user, asset_id=asset_id,
            tool_name="get_authorized_keys", command="authorized_keys_audit",
            params={}, timeout=120,
        )
        keys = raw.get("authorized_keys", [])
        return [
            {"key_fingerprint": k.get("key_fingerprint"), "user": k.get("user"),
             "last_used": k.get("last_used"), "stale": k.get("stale")}
            for k in keys
        ]
    except ValueError as e:
        return [{"error": str(e)}]
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def get_ssl_certs(token: str, asset_id: str) -> list[dict[str, Any]]:
    """
    List TLS/SSL certificates found on a host. Returns subject, expiry, days remaining,
    issuer, and whether the chain is valid.
    """
    from app.services.host_intelligence_service import run_intelligence_tool

    user, db, db_cm = await _auth(token)
    try:
        await _assert_asset_owned(db, user, asset_id)
        raw = await run_intelligence_tool(
            db=db, user=user, asset_id=asset_id,
            tool_name="get_ssl_certs", command="ssl_cert_inspect",
            params={}, timeout=120,
        )
        certs = raw.get("certificates", [])
        return [
            {"subject": c.get("subject"), "expiry": c.get("expiry"),
             "days_remaining": c.get("days_remaining"), "issuer": c.get("issuer"),
             "chain_valid": c.get("chain_valid")}
            for c in certs
        ]
    except ValueError as e:
        return [{"error": str(e)}]
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def get_patch_status(token: str, asset_id: str) -> dict[str, Any]:
    """
    Return patch status: count of pending patches, CVEs addressed, last patched timestamp,
    and list of critical pending patches.
    """
    from app.services.host_intelligence_service import run_intelligence_tool

    user, db, db_cm = await _auth(token)
    try:
        await _assert_asset_owned(db, user, asset_id)
        raw = await run_intelligence_tool(
            db=db, user=user, asset_id=asset_id,
            tool_name="get_patch_status", command="audit_patch_status",
            params={}, timeout=120,
        )
        ps = raw.get("patch_status", raw)
        return {
            "pending_patches_count": ps.get("pending_patches_count"),
            "cves_addressed": ps.get("cves_addressed", []),
            "last_patched_at": ps.get("last_patched_at"),
            "critical_pending": ps.get("critical_pending"),
        }
    except ValueError as e:
        return {"error": str(e)}
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def get_host_full_context(token: str, asset_id: str) -> dict[str, Any]:
    """
    Return a complete host intelligence bundle by running all 15 individual tools in parallel.

    Keys: kernel, processes, cron_jobs, local_users, installed_packages,
    running_services, open_ports, security_posture, seccomp_policy,
    apparmor_profiles, selinux_policy, sudoers, authorized_keys, ssl_certs,
    patch_status.
    """
    import asyncio

    user, db, db_cm = await _auth(token)
    try:
        await _assert_asset_owned(db, user, asset_id)
    except ValueError as e:
        await db_cm.__aexit__(None, None, None)
        return {"error": str(e)}

    await db_cm.__aexit__(None, None, None)

    async def _call(tool_fn, *args, **kwargs):
        try:
            return await tool_fn(*args, **kwargs)
        except Exception as exc:
            return {"error": str(exc)}

    (
        kernel, processes, cron_jobs, local_users, installed_packages,
        running_services, open_ports, security_posture, seccomp_policy,
        apparmor_profiles, selinux_policy, sudoers, authorized_keys,
        ssl_certs, patch_status,
    ) = await asyncio.gather(
        _call(get_kernel_info, token, asset_id),
        _call(get_running_processes, token, asset_id),
        _call(get_cron_jobs, token, asset_id),
        _call(get_local_users, token, asset_id),
        _call(get_installed_packages, token, asset_id),
        _call(get_running_services, token, asset_id),
        _call(get_open_ports, token, asset_id),
        _call(get_security_posture, token, asset_id),
        _call(get_seccomp_policy, token, asset_id),
        _call(get_apparmor_profiles, token, asset_id),
        _call(get_selinux_policy, token, asset_id),
        _call(get_sudoers, token, asset_id),
        _call(get_authorized_keys, token, asset_id),
        _call(get_ssl_certs, token, asset_id),
        _call(get_patch_status, token, asset_id),
    )

    return {
        "asset_id": asset_id,
        "kernel": kernel, "processes": processes, "cron_jobs": cron_jobs,
        "local_users": local_users, "installed_packages": installed_packages,
        "running_services": running_services, "open_ports": open_ports,
        "security_posture": security_posture, "seccomp_policy": seccomp_policy,
        "apparmor_profiles": apparmor_profiles, "selinux_policy": selinux_policy,
        "sudoers": sudoers, "authorized_keys": authorized_keys,
        "ssl_certs": ssl_certs, "patch_status": patch_status,
    }
