import asyncio
from datetime import datetime, timezone
from ._client import get_identity_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    compartment_id = parameters.get("compartment_id", "")

    if not creds:
        return {"action": "delete_compartment", "compartment_id": compartment_id, "mock": True}

    client = get_identity_client(creds)
    loop = asyncio.get_running_loop()

    def _call():
        import oci.pagination
        sub_comps = oci.pagination.list_call_get_all_results(
            client.list_compartments, compartment_id=compartment_id
        ).data
        active_sub = [c for c in sub_comps if c.lifecycle_state not in ("DELETED",)]
        if active_sub:
            raise ValueError(
                f"Compartment has {len(active_sub)} active sub-compartment(s). "
                "Remove all resources before deleting."
            )
        client.delete_compartment(compartment_id)

    await loop.run_in_executor(None, _call)
    return {
        "action": "delete_compartment",
        "compartment_id": compartment_id,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "delete_compartment is destructive; no rollback available"}
