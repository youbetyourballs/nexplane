"""
Smoke test zombie reaper.

Terminates EC2 instances and cleans Tailscale nodes left alive when a smoke
test run died mid-execution (crash, Docker restart, network loss, etc.).

Safety: any instance whose ID appears in an active smoke_test_runs record
(status='running') is considered live and will not be touched.
"""
import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy import select

logger = logging.getLogger(__name__)

# Instance name patterns created by run_on_ec2.py / test_aws_live.py
_ZOMBIE_NAME_PREFIXES = (
    "nxp-ec2-test-runner",
    "nexplane-smoke-",
)

# How old an instance must be before it's considered a zombie.
# Longest smoke phase (full Windows DC provision from scratch) is ~2h.
_ZOMBIE_AGE_HOURS = 3

# Tailscale nodes registered by smoke runners are prefixed with these names.
_TS_ZOMBIE_PREFIXES = (
    "nexplane-smoke-",
    "nxp-ec2-test-runner",
)


async def reap_smoke_zombies(db_factory) -> None:
    """Main entry point — called by APScheduler every 30 min."""
    try:
        async with db_factory() as db:
            active_instance_ids = await _get_active_runner_ids(db)

        await _reap_ec2_instances(active_instance_ids)
        await _reap_tailscale_nodes(active_instance_ids)
    except Exception as e:
        logger.error(f"smoke_reaper: unhandled error: {e}", exc_info=True)


async def _get_active_runner_ids(db) -> set[str]:
    """Return runner_instance_ids from currently active smoke runs."""
    from app.models.smoke_test_run import SmokeTestRun
    result = await db.execute(
        select(SmokeTestRun.runner_instance_id).where(
            SmokeTestRun.status == "running",
            SmokeTestRun.runner_instance_id.isnot(None),
        )
    )
    return {row[0] for row in result.fetchall() if row[0]}


async def _get_aws_client(service: str):
    """Build a boto3 client using the platform's AWS connector credentials."""
    import boto3
    try:
        import asyncio, sys
        if "/app" not in sys.path:
            sys.path.insert(0, "/app")
        from app.database import AsyncSessionLocal
        from app.models.connector import Connector
        from app.models.connector_credential import ConnectorCredential
        from app.services.secrets_service import SecretsService
        from app.config import settings
        from sqlalchemy import select as _select

        async with AsyncSessionLocal() as db:
            row = (await db.execute(
                _select(ConnectorCredential.credentials_encrypted)
                .join(Connector, Connector.id == ConnectorCredential.connector_id)
                .where(Connector.connector_type == "aws")
            )).fetchone()
            if not row:
                return None
            creds = SecretsService(settings.SECRET_KEY).decrypt_json(row[0])

        return boto3.client(
            service,
            region_name=creds.get("region", "us-east-1"),
            aws_access_key_id=creds["access_key_id"],
            aws_secret_access_key=creds["secret_access_key"],
        )
    except Exception as e:
        logger.error(f"smoke_reaper: failed to build AWS client: {e}")
        return None


async def _reap_ec2_instances(active_instance_ids: set[str]) -> None:
    import asyncio
    ec2 = await _get_aws_client("ec2")
    if not ec2:
        return

    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=_ZOMBIE_AGE_HOURS)

        # Find all running/pending instances with zombie name patterns
        filters = [
            {"Name": "instance-state-name", "Values": ["running", "pending", "stopping"]},
        ]
        resp = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: ec2.describe_instances(Filters=filters)
        )

        to_terminate = []
        for reservation in resp["Reservations"]:
            for inst in reservation["Instances"]:
                iid = inst["InstanceId"]
                launch_time = inst.get("LaunchTime")
                name = next(
                    (t["Value"] for t in inst.get("Tags", []) if t["Key"] == "Name"),
                    "",
                )

                # Skip if protected by active run
                if iid in active_instance_ids:
                    continue

                # Skip if name doesn't match zombie patterns
                if not any(name.startswith(p) for p in _ZOMBIE_NAME_PREFIXES):
                    continue

                # Skip if not old enough
                if launch_time and launch_time > cutoff:
                    continue

                to_terminate.append(iid)
                logger.warning(
                    f"smoke_reaper: zombie EC2 {iid} ({name}) "
                    f"launched {launch_time}, terminating"
                )

        if to_terminate:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: ec2.terminate_instances(InstanceIds=to_terminate)
            )
            logger.info(f"smoke_reaper: terminated {len(to_terminate)} zombie instance(s): {to_terminate}")
        else:
            logger.debug("smoke_reaper: no zombie EC2 instances found")

    except Exception as e:
        logger.error(f"smoke_reaper: EC2 reap failed: {e}", exc_info=True)


async def _reap_tailscale_nodes(active_instance_ids: set[str]) -> None:
    """Remove Tailscale nodes registered by zombie smoke runners."""
    import asyncio
    try:
        import sys
        if "/app" not in sys.path:
            sys.path.insert(0, "/app")
        from app.database import AsyncSessionLocal
        from app.models.connector import Connector
        from app.models.connector_credential import ConnectorCredential
        from app.services.secrets_service import SecretsService
        from app.config import settings
        from sqlalchemy import select as _select
        import httpx

        async with AsyncSessionLocal() as db:
            row = (await db.execute(
                _select(ConnectorCredential.credentials_encrypted)
                .join(Connector, Connector.id == ConnectorCredential.connector_id)
                .where(Connector.connector_type == "tailscale")
            )).fetchone()
            if not row:
                return
            ts_creds = SecretsService(settings.SECRET_KEY).decrypt_json(row[0])

        api_key = ts_creds.get("api_key", "")
        if not api_key:
            logger.debug("smoke_reaper: no Tailscale API key, skipping node reap")
            return

        cutoff = datetime.now(timezone.utc) - timedelta(hours=_ZOMBIE_AGE_HOURS)

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://api.tailscale.com/api/v2/tailnet/-/devices",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=15,
            )
            if resp.status_code != 200:
                logger.warning(f"smoke_reaper: Tailscale API returned {resp.status_code}")
                return

            devices = resp.json().get("devices", [])
            for device in devices:
                name = device.get("name", "").split(".")[0]  # strip tailnet suffix
                node_id = device.get("id", "")
                last_seen_str = device.get("lastSeen", "")

                if not any(name.startswith(p) for p in _TS_ZOMBIE_PREFIXES):
                    continue

                try:
                    last_seen = datetime.fromisoformat(last_seen_str.replace("Z", "+00:00"))
                except ValueError:
                    continue

                if last_seen > cutoff:
                    continue

                logger.warning(
                    f"smoke_reaper: removing stale Tailscale node {name} "
                    f"(last seen {last_seen_str})"
                )
                await client.delete(
                    f"https://api.tailscale.com/api/v2/device/{node_id}",
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=10,
                )

    except Exception as e:
        logger.error(f"smoke_reaper: Tailscale reap failed: {e}", exc_info=True)
