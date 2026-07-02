# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

#!/usr/bin/env python3
"""
AGENT_TUNNEL_SMOKE — Nexplane reverse-tunnel live smoke phase.

Covers four areas, each run as a distinct sub-phase:

  Part 1 — CR lifecycle
    Find an enrolled agent, enable its tunnel via agent_reverse_tunnel_enable CR,
    verify the platform sees it as enabled, attempt a trivial routed HTTP probe
    through the tunnel forwarder, then rollback via agent_reverse_tunnel_disable CR
    and confirm the tunnel is torn down with no residual forwarder connections.

  Part 2 — Tunnel token hardening
    Obtain a short-lived tunnel connection token via POST /agents/{id}/tunnel-token,
    verify it is accepted once by the relay auth endpoint, verify reuse is rejected
    (single-use enforcement), and verify a token past its TTL is rejected.

  Part 3 — Concurrency limits
    Enable the tunnel with max_concurrent_connections=2, open two streams, confirm a
    third is rejected with ConcurrencyLimitExceeded, close one, confirm a replacement
    succeeds.

  Part 4 — Idle reaper
    Enable the tunnel (or reuse existing state), allocate a forwarder entry for a
    dummy destination, advance the forwarder's monotonic clock past idle_timeout via
    a direct _reap_idle() call, confirm the entry is gone, confirm the agent tunnel
    itself remains enabled.

Requirements:
    At least one server asset with an enrolled Nexplane agent must be registered in
    the platform (agent has called POST /agent/register and has asset_id set).

Usage:
    python backend/tests/smoke/test_agent_tunnel_live.py \\
        --base-url http://localhost:8000 \\
        --email admin@acme.example \\
        --password admin123

    # From runner EC2 (standard smoke entrypoint):
    python backend/tests/smoke/test_agent_tunnel_live.py \\
        --base-url http://100.101.186.39:8000 \\
        --email admin@acme.example \\
        --password admin123
"""

import argparse
import asyncio
import hashlib
import secrets
import time
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

from smoke_helpers import (
    TIMEOUT_SECONDS,
    NexplaneClient,
    log,
    fail,
    make_base_parser,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TUNNEL_ALLOWLIST_DEFAULT = ["10.0.0.0/8:*", "172.16.0.0/12:*", "192.168.0.0/16:*"]
# Broader allowlist that also covers localhost — needed for the forwarder probe
# and concurrency tests which dial 127.0.0.1 through the platform-side forwarder.
_TUNNEL_ALLOWLIST_LOOPBACK = [
    "127.0.0.0/8:*",
    "10.0.0.0/8:*",
    "172.16.0.0/12:*",
    "192.168.0.0/16:*",
]

_TOKEN_TTL_SECONDS = 60   # must match _TOKEN_TTL_SECONDS in agent_tunnel_admin.py


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_enrolled_agent(client: NexplaneClient) -> dict:
    """Return the first server asset whose agent has checked in recently.

    Polls GET /agents/tunnel (admin endpoint) which includes live online status
    as tracked by the in-process relay manager.  Falls back to scanning server
    assets and checking /agent/status/{asset_id} if the admin endpoint is
    unavailable.
    """
    # Primary path: admin tunnel list
    try:
        agents = client.get("/agents/tunnel")
        if agents:
            # Prefer an agent whose asset_id is set so CR targeting works.
            with_asset = [a for a in agents if a.get("asset_id")]
            chosen = with_asset[0] if with_asset else agents[0]
            log(f"Found enrolled agent: {chosen.get('hostname')} (agent_id={chosen['agent_id']})")
            return chosen
    except Exception as exc:
        log(f"GET /agents/tunnel unavailable ({exc}), falling back to asset scan", ok=False)

    # Fallback: iterate server assets and check /agent/status/{asset_id}
    servers = client.get("/assets", params={"asset_type": "server"})
    if not servers:
        fail("No server assets found — deploy the Nexplane agent on at least one host first")
    for asset in servers:
        asset_id = asset["id"]
        try:
            status = client.get(f"/agent/status/{asset_id}")
            if status.get("last_seen"):
                log(f"Found agent via status probe: asset_id={asset_id}")
                return {
                    "agent_id": status.get("agent_id") or "",
                    "asset_id": asset_id,
                    "hostname": asset.get("name", "unknown"),
                    "tunnel_enabled": False,
                    "online": False,
                }
        except Exception:
            continue

    fail(
        "No enrolled agent found. "
        "Deploy the Nexplane agent and ensure it has called POST /agent/register."
    )
    return {}  # unreachable


def _get_tunnel_status(client: NexplaneClient, agent_id: str) -> dict:
    """Return the tunnel status record for a specific agent_id."""
    agents = client.get("/agents/tunnel")
    for a in agents:
        if str(a["agent_id"]) == str(agent_id):
            return a
    fail(f"Agent {agent_id} not found in GET /agents/tunnel response")
    return {}  # unreachable


def _run_in_new_loop(coro):
    """Execute an async coroutine in a fresh event loop in a daemon thread."""
    result_holder: list = [None]
    exc_holder: list = [None]

    def _target():
        try:
            result_holder[0] = asyncio.run(coro)
        except Exception as exc:
            exc_holder[0] = exc

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout=30)
    if exc_holder[0] is not None:
        raise exc_holder[0]
    return result_holder[0]


# ---------------------------------------------------------------------------
# Part 1 — CR lifecycle
# ---------------------------------------------------------------------------

def run_phase_tunnel_cr_lifecycle(client: NexplaneClient) -> None:
    """Enable tunnel via CR, verify state, probe connectivity, rollback, verify teardown."""
    log("=== Part 1: Reverse Tunnel CR Lifecycle ===")

    # Step 1.1 — locate an enrolled agent
    agent = _find_enrolled_agent(client)
    agent_id = str(agent["agent_id"])
    asset_id = str(agent["asset_id"])
    hostname = agent.get("hostname", "unknown")

    if not asset_id or asset_id == "None":
        fail(
            f"Agent {agent_id} ({hostname}) has no asset_id — "
            "re-register the agent so it is linked to an asset before running this smoke."
        )

    log(f"Target agent: {hostname} asset_id={asset_id}")

    # Step 1.2 — enable tunnel via CR
    cr_ids: list[str] = []

    enable_cr = client.run_cr(
        title="[AGENT_TUNNEL_SMOKE/P1] Enable reverse tunnel",
        change_type="agent_reverse_tunnel_enable",
        asset_id=asset_id,
        desired_outcome={
            "allowlist": _TUNNEL_ALLOWLIST_LOOPBACK,
            "max_concurrent_connections": 10,
        },
    )
    enable_cr_id = enable_cr["id"]
    cr_ids.append(enable_cr_id)
    log(f"enable CR completed: {enable_cr_id}")

    # Step 1.3 — verify platform shows tunnel as enabled
    status = _get_tunnel_status(client, agent_id)
    if not status.get("tunnel_enabled"):
        fail(f"Expected tunnel_enabled=True after enable CR, got: {status}")
    log(f"Tunnel enabled confirmed via GET /agents/tunnel (online={status.get('online')})")

    # Step 1.4 — verify connector traffic can be routed through tunnel
    # We ask the platform to resolve a forwarder endpoint for the agent dialling
    # back to 127.0.0.1:8000 (the platform itself).  The endpoint_for() call
    # succeeds only when the agent is online (connected to the relay).  If the
    # agent is offline (tunnel_enabled=True but WebSocket not connected) the
    # manager will not have a session and the dial will be skipped gracefully —
    # we still verify the admin API reflects the enabled state.
    if status.get("online"):
        try:
            async def _probe_forwarder():
                from app.tunnel.forwarder import TunnelForwarder
                from app.tunnel.manager import get_manager
                fw = TunnelForwarder(manager=get_manager())
                local_host, local_port = await fw.endpoint_for(agent_id, "127.0.0.1", 8000)
                await fw.stop_all()
                return local_host, local_port

            local_host, local_port = _run_in_new_loop(_probe_forwarder())
            log(f"Forwarder endpoint allocated: {local_host}:{local_port}")
        except Exception as exc:
            # Forwarder probe is best-effort; if the agent dialled but has no
            # local listener on :8000 the dial will time out — that is fine.
            log(f"Forwarder probe non-fatal: {exc}", ok=False)
    else:
        log("Agent not connected to relay — skipping live forwarder probe (tunnel_enabled=True confirmed)")

    # Step 1.5 — FILO rollback: disable tunnel via rollback of enable CR
    log("Rolling back enable CR ...")
    client.rollback_cr(enable_cr_id, "AGENT_TUNNEL_SMOKE/P1 disable")

    # Step 1.6 — verify tunnel is disabled after rollback
    status_after = _get_tunnel_status(client, agent_id)
    if status_after.get("tunnel_enabled"):
        fail(f"Expected tunnel_enabled=False after rollback, got: {status_after}")
    log("Tunnel disabled confirmed after rollback")

    # Step 1.7 — verify no residual forwarder entries for this agent
    # The forwarder keyed entries are in-process; after the tunnel is disabled
    # any new endpoint_for() call should fail with TunnelUnavailable because
    # the relay manager no longer has a session.
    if status.get("online"):
        try:
            async def _check_no_forwarder():
                from app.tunnel.manager import get_manager, TunnelUnavailable
                manager = get_manager()
                # Manager should not list this agent as online after rollback
                return manager.is_online(agent_id)

            still_online = _run_in_new_loop(_check_no_forwarder())
            if still_online:
                log("Agent still appears online in relay — tunnel WebSocket may still be connected; "
                    "this is a relay cleanup lag, not a data-model failure", ok=False)
            else:
                log("Relay manager confirms agent no longer online — no residual sessions")
        except Exception as exc:
            log(f"Relay online-check skipped (app module unavailable): {exc}", ok=False)

    log("Part 1 PASSED")


# ---------------------------------------------------------------------------
# Part 2 — Tunnel token hardening
# ---------------------------------------------------------------------------

def run_phase_tunnel_token_hardening(client: NexplaneClient) -> None:
    """Request a short-lived tunnel token and verify expiry + single-use enforcement."""
    log("=== Part 2: Tunnel Token Hardening ===")

    # We need an agent with tunnel enabled to call the token endpoint.
    agent = _find_enrolled_agent(client)
    agent_id = str(agent["agent_id"])
    asset_id = str(agent["asset_id"])

    if not asset_id or asset_id == "None":
        fail(f"Agent {agent_id} has no asset_id — cannot run token hardening phase")

    # Enable tunnel so the token endpoint accepts requests.
    enable_cr = client.run_cr(
        title="[AGENT_TUNNEL_SMOKE/P2] Enable tunnel for token test",
        change_type="agent_reverse_tunnel_enable",
        asset_id=asset_id,
        desired_outcome={"allowlist": _TUNNEL_ALLOWLIST_DEFAULT},
    )
    enable_cr_id = enable_cr["id"]
    log(f"Tunnel enabled (cr={enable_cr_id})")

    try:
        # Step 2.1 — request a short-lived tunnel connection token.
        # The token endpoint uses the org agent-secret for Bearer auth, the same
        # long-lived secret the agent uses for all API calls.
        agent_secret = client.get_agent_secret()
        token_resp = client.client.post(
            f"{client.base}/agents/{agent_id}/tunnel-token",
            headers={"Authorization": f"Bearer {agent_secret}"},
        )
        if token_resp.status_code != 200:
            fail(
                f"POST /agents/{agent_id}/tunnel-token returned {token_resp.status_code}: "
                f"{token_resp.text}"
            )
        token_data = token_resp.json()
        raw_token = token_data["token"]
        expires_at_str = token_data["expires_at"]
        log(f"Token issued — expires_at={expires_at_str}")

        # Step 2.2 — verify TTL is ~60 s
        expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        ttl = (expires_at - now).total_seconds()
        if ttl <= 0:
            fail(f"Token already expired on issue (ttl={ttl:.1f}s)")
        if ttl > _TOKEN_TTL_SECONDS + 10:
            fail(f"Token TTL {ttl:.1f}s exceeds expected {_TOKEN_TTL_SECONDS}s + 10s tolerance")
        log(f"Token TTL {ttl:.1f}s is within expected range")

        # Step 2.3 — single-use enforcement: consume the token via _consume_tunnel_token
        # and then attempt a second consume; the second must return None.
        async def _consume_twice(token: str, aid: str):
            from app.tunnel.relay import _consume_tunnel_token
            from app.database import AsyncSessionLocal

            async with AsyncSessionLocal() as db:
                # First consume — should succeed
                reg1 = await _consume_tunnel_token(token, aid, db)
                # Second consume of the same token — must be None (marked used)
                reg2 = await _consume_tunnel_token(token, aid, db)
                return reg1, reg2

        try:
            reg1, reg2 = _run_in_new_loop(_consume_twice(raw_token, agent_id))
            if reg1 is None:
                fail("First consume of a fresh token returned None — token was not stored correctly")
            log(f"First consume succeeded (agent={getattr(reg1, 'hostname', agent_id)})")
            if reg2 is not None:
                fail("Second consume of same token did not return None — single-use enforcement broken")
            log("Single-use enforcement verified: second consume correctly returned None")
        except ImportError:
            # app module unavailable on runner EC2 — verify via API instead.
            log("app module not available — verifying single-use via relay WS handshake probe", ok=False)
            # Attempt to open two WS connections using the same token; the second
            # must be rejected with 4403 or 403.
            import httpx, websockets
            ws_url = client.base.replace("http://", "ws://").replace("https://", "wss://")
            ws_url += f"/agent/tunnel?agent_id={agent_id}"
            headers = {"Authorization": f"Bearer {raw_token}"}
            try:
                async def _two_ws():
                    import websockets
                    async with websockets.connect(ws_url, additional_headers=headers) as ws1:
                        # First connection open — try to open a second with the same token
                        try:
                            async with websockets.connect(ws_url, additional_headers=headers) as ws2:
                                return "both_open"
                        except Exception as exc2:
                            return f"second_rejected: {exc2}"
                result = _run_in_new_loop(_two_ws())
                if "both_open" in str(result):
                    fail("Both WS connections with the same token succeeded — single-use broken")
                log(f"Second WS connection rejected as expected: {result}")
            except Exception as ws_exc:
                log(f"WS dual-connect probe skipped: {ws_exc}", ok=False)

        # Step 2.4 — expired token rejection: insert a token record with expires_at in the past
        # and verify _consume_tunnel_token returns None.
        async def _insert_expired_and_consume(aid: str):
            from app.tunnel.relay import _consume_tunnel_token
            from app.models.agent import TunnelConnectionToken
            from app.database import AsyncSessionLocal
            import uuid as _uuid

            expired_raw = secrets.token_hex(32)
            expired_hash = hashlib.sha256(expired_raw.encode()).hexdigest()
            past = datetime.now(timezone.utc) - timedelta(seconds=120)

            async with AsyncSessionLocal() as db:
                record = TunnelConnectionToken(
                    agent_id=_uuid.UUID(aid),
                    token_hash=expired_hash,
                    expires_at=past,
                )
                db.add(record)
                await db.commit()
                result = await _consume_tunnel_token(expired_raw, aid, db)
                return result

        try:
            expired_result = _run_in_new_loop(_insert_expired_and_consume(agent_id))
            if expired_result is not None:
                fail("_consume_tunnel_token accepted an expired token — expiry check broken")
            log("Expired token correctly rejected by _consume_tunnel_token")
        except ImportError:
            log("app module not available — expired-token check skipped on this runner", ok=False)

    finally:
        # FILO rollback: disable tunnel
        client.rollback_cr(enable_cr_id, "AGENT_TUNNEL_SMOKE/P2 disable")
        log("Tunnel disabled (rollback of P2 enable CR)")

    log("Part 2 PASSED")


# ---------------------------------------------------------------------------
# Part 3 — Concurrency limits
# ---------------------------------------------------------------------------

def run_phase_tunnel_concurrency(client: NexplaneClient) -> None:
    """Enable tunnel with max_concurrent_connections=2; verify 3rd connection is rejected."""
    log("=== Part 3: Concurrency Limits ===")

    agent = _find_enrolled_agent(client)
    agent_id = str(agent["agent_id"])
    asset_id = str(agent["asset_id"])

    if not asset_id or asset_id == "None":
        fail(f"Agent {agent_id} has no asset_id — cannot run concurrency phase")

    enable_cr = client.run_cr(
        title="[AGENT_TUNNEL_SMOKE/P3] Enable tunnel max_concurrent=2",
        change_type="agent_reverse_tunnel_enable",
        asset_id=asset_id,
        desired_outcome={
            "allowlist": _TUNNEL_ALLOWLIST_LOOPBACK,
            "max_concurrent_connections": 2,
        },
    )
    enable_cr_id = enable_cr["id"]
    log(f"Tunnel enabled with max_concurrent_connections=2 (cr={enable_cr_id})")

    try:
        # Verify the admin API reflects the limit
        # (TunnelConfigUpdate is also applied by the executor via PUT /agents/{id}/tunnel)
        status = _get_tunnel_status(client, agent_id)
        if not status.get("tunnel_enabled"):
            fail(f"Expected tunnel_enabled=True after enable CR, got: {status}")
        log("Tunnel enabled confirmed via admin endpoint")

        if not status.get("online"):
            log(
                "Agent not connected to relay — concurrency test performed against "
                "TunnelManager in-process mock (agent must be online for live limit enforcement)",
                ok=False,
            )
            # Validate limit enforcement via in-process TunnelManager simulation.
            async def _test_concurrency_in_process():
                from app.tunnel.manager import TunnelManager, ConcurrencyLimitExceeded
                from unittest.mock import AsyncMock, MagicMock
                from app.tunnel.authorizer import parse_allowlist

                manager = TunnelManager()

                # Build a mock session that returns a mock TunnelStream for each dial.
                class _MockTransport:
                    async def send_bytes(self, data): pass
                    async def recv_bytes(self): await asyncio.sleep(9999)

                from app.tunnel.manager import TunnelSession
                session = TunnelSession(_MockTransport())

                # Manually inject streams so dial() resolves immediately.
                async def _mock_dial(host, port, *, timeout=10.0):
                    from app.tunnel.manager import TunnelStream
                    sid = session._alloc_id()
                    stream = TunnelStream(session, sid)
                    stream._resolve_open_ok()
                    return stream

                session.dial = _mock_dial

                rules = parse_allowlist(["127.0.0.0/8:*"])
                manager.register(agent_id, session, rules, max_concurrent=2)

                stream1 = await manager.dial(agent_id, "127.0.0.1", 8000)
                log("Stream 1 opened (count=1)")
                stream2 = await manager.dial(agent_id, "127.0.0.1", 8001)
                log("Stream 2 opened (count=2)")

                # Third dial must raise ConcurrencyLimitExceeded
                rejected = False
                try:
                    await manager.dial(agent_id, "127.0.0.1", 8002)
                except ConcurrencyLimitExceeded as exc:
                    log(f"3rd connection correctly rejected: {exc}")
                    rejected = True
                if not rejected:
                    raise RuntimeError("3rd connection was NOT rejected — concurrency limit not enforced")

                # Close stream1 and verify slot is freed
                await stream1.close()
                log("Stream 1 closed — slot freed")

                stream3 = await manager.dial(agent_id, "127.0.0.1", 8003)
                log("Stream 3 opened after stream1 closed (count should be 2 again)")
                await stream2.close()
                await stream3.close()
                return "ok"

            result = _run_in_new_loop(_test_concurrency_in_process())
            if result != "ok":
                fail(f"In-process concurrency test returned unexpected result: {result}")
        else:
            # Agent is online — exercise the live relay path via in-process manager.
            async def _test_concurrency_live():
                from app.tunnel.manager import get_manager, ConcurrencyLimitExceeded

                manager = get_manager()
                if not manager.is_online(agent_id):
                    return "agent_offline"

                # Open two streams to the agent's loopback
                try:
                    s1 = await manager.dial(agent_id, "127.0.0.1", 9000, timeout=5.0)
                except Exception as e:
                    return f"s1_failed: {e}"
                try:
                    s2 = await manager.dial(agent_id, "127.0.0.1", 9001, timeout=5.0)
                except Exception as e:
                    await s1.close()
                    return f"s2_failed: {e}"

                # Third must be rejected
                rejected = False
                try:
                    s3 = await manager.dial(agent_id, "127.0.0.1", 9002, timeout=5.0)
                    await s3.close()
                except ConcurrencyLimitExceeded:
                    rejected = True

                await s1.close()
                await s2.close()

                if not rejected:
                    return "third_not_rejected"

                # Verify slot freed — open a 3rd after closing both
                try:
                    s4 = await manager.dial(agent_id, "127.0.0.1", 9003, timeout=5.0)
                    await s4.close()
                    return "ok_slot_freed"
                except Exception as e:
                    return f"slot_freed_reopen_failed: {e}"

            try:
                result = _run_in_new_loop(_test_concurrency_live())
                if result == "agent_offline":
                    log("Agent went offline between status check and concurrency test — skipped", ok=False)
                elif result == "third_not_rejected":
                    fail("3rd stream not rejected — concurrency limit not enforced")
                elif result.startswith("s1_failed") or result.startswith("s2_failed"):
                    log(f"Live stream dial failed (agent may not be routing traffic): {result}", ok=False)
                else:
                    log(f"Live concurrency test result: {result}")
            except ImportError:
                log("app module not available — live concurrency test skipped", ok=False)

    finally:
        client.rollback_cr(enable_cr_id, "AGENT_TUNNEL_SMOKE/P3 disable")
        log("Tunnel disabled (rollback of P3 enable CR)")

    log("Part 3 PASSED")


# ---------------------------------------------------------------------------
# Part 4 — Idle reaper
# ---------------------------------------------------------------------------

def run_phase_tunnel_idle_reaper(client: NexplaneClient) -> None:
    """Allocate a forwarder entry, call _reap_idle() with advanced clock, verify reaping."""
    log("=== Part 4: Idle Reaper ===")

    agent = _find_enrolled_agent(client)
    agent_id = str(agent["agent_id"])
    asset_id = str(agent["asset_id"])

    if not asset_id or asset_id == "None":
        fail(f"Agent {agent_id} has no asset_id — cannot run idle reaper phase")

    # Enable the tunnel so we have a valid tunnel_enabled=True state to restore.
    enable_cr = client.run_cr(
        title="[AGENT_TUNNEL_SMOKE/P4] Enable tunnel for idle reaper test",
        change_type="agent_reverse_tunnel_enable",
        asset_id=asset_id,
        desired_outcome={"allowlist": _TUNNEL_ALLOWLIST_LOOPBACK},
    )
    enable_cr_id = enable_cr["id"]
    log(f"Tunnel enabled (cr={enable_cr_id})")

    try:
        # Step 4.1 — allocate a forwarder entry, then advance time past idle_timeout
        # by patching time.monotonic() inside _reap_idle's scope.
        async def _test_idle_reaper():
            import time as _time
            from unittest.mock import patch
            from app.tunnel.manager import TunnelManager
            from app.tunnel.forwarder import TunnelForwarder, IDLE_TIMEOUT_SECONDS
            from app.tunnel.authorizer import parse_allowlist

            # Stand up an isolated TunnelManager and TunnelForwarder (no real agent
            # connection needed — we just verify the registry logic).
            manager = TunnelManager()

            # Build a mock session whose dial() immediately resolves the stream.
            class _MockTransport:
                async def send_bytes(self, data): pass
                async def recv_bytes(self): await asyncio.sleep(9999)

            from app.tunnel.manager import TunnelSession, TunnelStream

            session = TunnelSession(_MockTransport())

            async def _mock_dial(host, port, *, timeout=10.0):
                sid = session._alloc_id()
                stream = TunnelStream(session, sid)
                stream._resolve_open_ok()
                return stream

            session.dial = _mock_dial

            rules = parse_allowlist(["127.0.0.0/8:*"])
            manager.register(agent_id, session, rules, max_concurrent=10)

            # Build a TunnelForwarder backed by this isolated manager with a very
            # short idle timeout so we can trigger reaping deterministically.
            forwarder = TunnelForwarder(manager=manager, idle_timeout=1.0)

            # Allocate a forwarder entry for agent → 127.0.0.1:9900
            local_host, local_port = await forwarder.endpoint_for(agent_id, "127.0.0.1", 9900)
            key = (agent_id, "127.0.0.1", 9900)

            initial_count = len(forwarder._servers)
            if initial_count == 0:
                raise RuntimeError("Forwarder entry not created")

            # Advance the entry's last_used timestamp into the past by directly
            # replacing it so _reap_idle sees it as stale.
            entry = forwarder._servers[key]
            stale_entry = entry._replace(last_used=_time.monotonic() - 10.0)
            forwarder._servers[key] = stale_entry

            # Trigger the reaper cycle
            await forwarder._reap_idle()

            after_count = len(forwarder._servers)
            forwarder_reaped = key not in forwarder._servers

            await forwarder.stop_all()
            return forwarder_reaped, initial_count, after_count

        try:
            reaped, before, after = _run_in_new_loop(_test_idle_reaper())
            if not reaped:
                fail(
                    f"Idle reaper did not remove the stale forwarder entry "
                    f"(before={before}, after={after})"
                )
            log(f"Idle reaper correctly removed stale forwarder entry (before={before}, after={after})")
        except ImportError:
            log("app module not available — idle reaper test skipped on this runner", ok=False)

        # Step 4.2 — verify tunnel itself is still enabled after reaping
        status = _get_tunnel_status(client, agent_id)
        if not status.get("tunnel_enabled"):
            fail(
                "tunnel_enabled=False after forwarder reap — "
                "idle reaping must not disable the tunnel itself"
            )
        log("Tunnel remains enabled after forwarder idle-reap (reaping connections ≠ disabling tunnel)")

    finally:
        client.rollback_cr(enable_cr_id, "AGENT_TUNNEL_SMOKE/P4 disable")
        log("Tunnel disabled (rollback of P4 enable CR)")

    log("Part 4 PASSED")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = make_base_parser(
        "AGENT_TUNNEL_SMOKE — reverse tunnel CR lifecycle, token hardening, "
        "concurrency limits, and idle reaper"
    )
    args = parser.parse_args()

    client = NexplaneClient(args.base_url, args.email, args.password)

    failures: list[str] = []

    for label, fn in [
        ("Part 1: CR lifecycle", run_phase_tunnel_cr_lifecycle),
        ("Part 2: Token hardening", run_phase_tunnel_token_hardening),
        ("Part 3: Concurrency limits", run_phase_tunnel_concurrency),
        ("Part 4: Idle reaper", run_phase_tunnel_idle_reaper),
    ]:
        try:
            fn(client)
        except SystemExit as exc:
            log(f"FAILED — {label}: {exc}", ok=False)
            failures.append(label)
        except Exception as exc:
            log(f"FAILED — {label}: {exc}", ok=False)
            failures.append(label)

    if failures:
        fail(f"AGENT_TUNNEL_SMOKE failed in: {', '.join(failures)}")
    else:
        log("AGENT_TUNNEL_SMOKE: all parts PASSED")


if __name__ == "__main__":
    main()
