"""Discover which services/systems consume a given credential before rotation."""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any


async def discover_iam_key_consumers(
    access_key_id: str,
    lookback_days: int = 90,
    region: str = "us-east-1",
    aws_access_key: str | None = None,
    aws_secret_key: str | None = None,
) -> dict[str, Any]:
    """
    Query CloudTrail to find which services made API calls using this access key.
    Returns: {"consumers": [{"service": "s3", "event_count": 12, "last_seen": "..."}], ...}
    """
    import boto3
    try:
        session = boto3.Session(
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key,
            region_name=region,
        )
        ct = session.client("cloudtrail")
        start = datetime.now(timezone.utc) - timedelta(days=lookback_days)

        events = []
        kwargs = {
            "LookupAttributes": [{"AttributeKey": "AccessKeyId", "AttributeValue": access_key_id}],
            "StartTime": start,
            "MaxResults": 50,
        }
        while True:
            resp = ct.lookup_events(**kwargs)
            events.extend(resp.get("Events", []))
            token = resp.get("NextToken")
            if not token or len(events) >= 200:
                break
            kwargs["NextToken"] = token

        # Aggregate by service
        service_counts: dict[str, dict] = {}
        for ev in events:
            svc = (ev.get("EventSource") or "").replace(".amazonaws.com", "")
            if not svc:
                continue
            if svc not in service_counts:
                service_counts[svc] = {"service": svc, "event_count": 0, "last_seen": None}
            service_counts[svc]["event_count"] += 1
            ev_time = ev.get("EventTime")
            if ev_time:
                ts = ev_time.isoformat() if hasattr(ev_time, "isoformat") else str(ev_time)
                if not service_counts[svc]["last_seen"] or ts > service_counts[svc]["last_seen"]:
                    service_counts[svc]["last_seen"] = ts

        return {
            "access_key_id": access_key_id,
            "lookback_days": lookback_days,
            "total_events": len(events),
            "consumers": list(service_counts.values()),
            "discovered_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        return {
            "access_key_id": access_key_id,
            "consumers": [],
            "error": str(e),
            "discovered_at": datetime.now(timezone.utc).isoformat(),
        }


async def discover_config_file_consumers(
    asset_ids: list[str],
    credential_hint: str,
) -> dict[str, Any]:
    """
    Use the agent's appdiscovery data to find config files referencing this credential.
    credential_hint: part of the credential (e.g. access key prefix like 'AKIA...')
    """
    from app.connectors.executors.nexplane_agent import _dispatch
    results = []
    for asset_id in asset_ids:
        try:
            result = await _dispatch.dispatch_agent_job(
                command="deep_discover",
                parameters={"grep_pattern": credential_hint, "search_paths": ["/etc", "/opt", "/home"]},
                asset_ids=[asset_id],
                timeout_seconds=60,
            )
            if result.get("matches"):
                results.append({"asset_id": asset_id, "matches": result["matches"]})
        except Exception:
            pass
    return {"config_consumers": results}
