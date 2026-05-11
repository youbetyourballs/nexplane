import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Delete an OCI VCN (and its Internet Gateway if present)."""
    creds = getattr(connector, "credentials", {})
    vcn_id = parameters.get("vcn_id", "")
    internet_gateway_id = parameters.get("internet_gateway_id", "")

    if not creds:
        return {"action": "delete_vcn", "vcn_id": vcn_id, "mock": True}

    if not vcn_id:
        return {"action": "delete_vcn", "skipped": True, "reason": "no vcn_id"}

    from ._client import get_network_client
    import oci

    network = get_network_client(creds)
    loop = asyncio.get_running_loop()

    # Remove IGW from route table first to allow IGW deletion
    if internet_gateway_id:
        try:
            vcn = await loop.run_in_executor(None, lambda: network.get_vcn(vcn_id).data)
            await loop.run_in_executor(
                None,
                lambda: network.update_route_table(
                    vcn.default_route_table_id,
                    oci.core.models.UpdateRouteTableDetails(route_rules=[]),
                ),
            )
        except Exception:
            pass
        try:
            await loop.run_in_executor(None, lambda: network.delete_internet_gateway(internet_gateway_id))
        except Exception:
            pass

    await loop.run_in_executor(None, lambda: network.delete_vcn(vcn_id))
    return {
        "action": "delete_vcn",
        "vcn_id": vcn_id,
        "deleted_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "delete_vcn has no rollback"}
