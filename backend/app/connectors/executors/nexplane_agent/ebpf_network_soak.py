# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import logging

from app.connectors.executors.nexplane_agent import _dispatch

logger = logging.getLogger(__name__)

_MAX_WARMUP_ATTEMPTS = 10
_WARMUP_RETRY_DELAY_SECONDS = 30


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    window_seconds = int(parameters.get("window_seconds", 60))
    job_timeout = window_seconds + 30

    result = None
    for attempt in range(_MAX_WARMUP_ATTEMPTS):
        result = await _dispatch.dispatch_agent_job(
            command="ebpf_network_soak",
            parameters=parameters,
            asset_ids=list(asset_ids),
            timeout_seconds=job_timeout,
        )
        flows = result.get("flows") or []
        if flows:
            if attempt > 0:
                logger.info(
                    "eBPF network soak: captured %d flows on attempt %d",
                    len(flows), attempt + 1,
                )
            break
        if attempt < _MAX_WARMUP_ATTEMPTS - 1:
            logger.warning(
                "eBPF network soak attempt %d/%d returned zero flows — "
                "waiting %ds before retry (eBPF hooks may still be warming up)",
                attempt + 1, _MAX_WARMUP_ATTEMPTS, _WARMUP_RETRY_DELAY_SECONDS,
            )
            await asyncio.sleep(_WARMUP_RETRY_DELAY_SECONDS)
        else:
            logger.warning(
                "eBPF network soak: zero flows after %d attempts — returning empty result; "
                "check ebpf_diagnostics from configure_ebpf_network for kernel/capability details",
                _MAX_WARMUP_ATTEMPTS,
            )

    result["_asset_ids"] = [str(a) for a in asset_ids]
    result["warmup_attempts"] = _MAX_WARMUP_ATTEMPTS if not (result.get("flows") or []) else (
        # record how many attempts it actually took
        result.get("warmup_attempts", 1)
    )
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "ebpf_network_soak creates no persistent state"}
