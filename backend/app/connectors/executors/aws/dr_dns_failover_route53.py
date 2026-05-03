import asyncio
from datetime import datetime, timezone


def _get_r53_client(creds: dict):
    from ._client import get_boto3_client
    return get_boto3_client(creds, "route53")


async def _real_execute(creds: dict, parameters: dict) -> dict:
    r53 = _get_r53_client(creds)
    loop = asyncio.get_event_loop()
    record_name = parameters["dns_record_id"]
    dr_endpoint = parameters["dr_endpoint"]
    hosted_zone = parameters["hosted_zone_id"]

    def _call():
        return r53.change_resource_record_sets(
            HostedZoneId=hosted_zone,
            ChangeBatch={
                "Changes": [{
                    "Action": "UPSERT",
                    "ResourceRecordSet": {
                        "Name": record_name,
                        "Type": "CNAME",
                        "SetIdentifier": "dr-primary",
                        "Weight": 100,
                        "TTL": 60,
                        "ResourceRecords": [{"Value": dr_endpoint}],
                    },
                }]
            },
        )

    await loop.run_in_executor(None, _call)
    return {
        "action":       "dr_dns_failover_route53",
        "dns_updated":  True,
        "new_endpoint": dr_endpoint,
        "record_name":  record_name,
        "executed_at":  datetime.now(timezone.utc).isoformat(),
    }


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {
            "action":       "dr_dns_failover_route53",
            "dns_updated":  True,
            "new_endpoint": parameters.get("dr_endpoint"),
            "mock":         True,
        }
    return await _real_execute(creds, parameters)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Revert DNS back to the original endpoint (stored in parameters['original_endpoint'])."""
    original = parameters.get("original_endpoint")
    if not original:
        return {"rolled_back": False, "reason": "original_endpoint not provided in parameters"}
    revert_params = {**parameters, "dr_endpoint": original}
    return await execute(revert_params, [], connector)
