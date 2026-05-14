from __future__ import annotations
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.policy_baseline import PolicyBaseline, DriftAlert


class PolicyBaselineService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def store(self, asset_id: str, policy_type: str, cr_id: str,
                    observation: dict, organization_id: str) -> tuple[str, str]:
        baseline = PolicyBaseline(
            organization_id=uuid.UUID(organization_id),
            asset_id=asset_id,
            policy_type=policy_type,
            cr_id=cr_id,
            observation=observation,
        )
        self.db.add(baseline)
        await self.db.commit()
        return (asset_id, str(baseline.id))

    async def get_latest(self, asset_id: str, policy_type: str) -> dict | None:
        result = await self.db.execute(
            select(PolicyBaseline)
            .where(PolicyBaseline.asset_id == asset_id, PolicyBaseline.policy_type == policy_type)
            .order_by(PolicyBaseline.created_at.desc())
            .limit(1)
        )
        baseline = result.scalar_one_or_none()
        if not baseline:
            return None
        return {"id": str(baseline.id), "observation": baseline.observation,
                "created_at": baseline.created_at.isoformat()}

    async def detect_drift(self, asset_id: str, policy_type: str,
                           current_observation: dict) -> dict:
        baseline = await self.get_latest(asset_id, policy_type)
        if not baseline:
            return {"new_behaviors": [], "baseline_missing": True}
        baseline_obs = baseline["observation"]
        new_behaviors = []
        if policy_type == "seccomp":
            baseline_syscalls = set(baseline_obs.get("observed_syscalls", []))
            current_syscalls = set(current_observation.get("observed_syscalls", []))
            new_behaviors = list(current_syscalls - baseline_syscalls)
        elif policy_type == "apparmor":
            baseline_denials = set(baseline_obs.get("observed_denials", []))
            current_denials = set(current_observation.get("observed_denials", []))
            new_behaviors = list(current_denials - baseline_denials)
        elif policy_type in ("iptables", "firewall"):
            baseline_conns = set(baseline_obs.get("observed_connections", []))
            current_conns = set(current_observation.get("observed_connections", []))
            new_behaviors = list(current_conns - baseline_conns)
        return {"new_behaviors": new_behaviors, "baseline_id": baseline["id"]}

    async def create_drift_alert(self, asset_id: str, policy_type: str,
                                  baseline_id: str, new_behaviors: list,
                                  organization_id: str) -> str:
        alert = DriftAlert(
            organization_id=uuid.UUID(organization_id),
            asset_id=asset_id,
            policy_type=policy_type,
            baseline_id=uuid.UUID(baseline_id),
            new_behaviors=new_behaviors,
        )
        self.db.add(alert)
        await self.db.commit()
        return str(alert.id)
