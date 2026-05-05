import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    zone_id = parameters.get('zone_id', '')
    name = parameters.get('name', '')
    record_type = parameters.get('record_type', 'A')
    values = parameters.get('values', [])
    ttl = parameters.get('ttl', 60)
    weight = parameters.get('weight', None)
    set_identifier = parameters.get('set_identifier', None)

    if not creds:
        return {
            "action": "upsert_route53_record",
            "zone_id": zone_id,
            "name": name,
            "type": record_type,
            "values": values,
            "mock": True,
        }

    from ._client import get_boto3_client
    r53 = get_boto3_client(creds, 'route53')
    loop = asyncio.get_event_loop()

    def _call():
        prior = None
        try:
            resp = r53.list_resource_record_sets(
                HostedZoneId=zone_id,
                StartRecordName=name,
                StartRecordType=record_type,
                MaxItems='1',
            )
            for rrs in resp['ResourceRecordSets']:
                if rrs['Name'].rstrip('.') == name.rstrip('.') and rrs['Type'] == record_type:
                    prior = rrs
                    break
        except Exception:
            pass

        rrs = {
            "Name": name,
            "Type": record_type,
            "TTL": ttl,
            "ResourceRecords": [{"Value": v} for v in values],
        }
        if weight is not None:
            rrs["Weight"] = weight
            rrs["SetIdentifier"] = set_identifier or name

        r53.change_resource_record_sets(
            HostedZoneId=zone_id,
            ChangeBatch={"Changes": [{"Action": "UPSERT", "ResourceRecordSet": rrs}]},
        )
        return prior

    prior_record = await loop.run_in_executor(None, _call)
    return {
        "action": "upsert_route53_record",
        "zone_id": zone_id,
        "name": name,
        "type": record_type,
        "values": values,
        "prior_record": prior_record,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    prior = execution_result.get('prior_record')
    if prior:
        return await execute({
            "zone_id": execution_result['zone_id'],
            "name": prior['Name'],
            "record_type": prior['Type'],
            "values": [r['Value'] for r in prior.get('ResourceRecords', [])],
            "ttl": prior.get('TTL', 60),
        }, [], connector)
    else:
        from app.connectors.executors.aws.delete_route53_record import execute as delete
        return await delete({
            "zone_id": execution_result['zone_id'],
            "name": execution_result['name'],
            "record_type": execution_result['type'],
            "values": execution_result['values'],
            "ttl": 60,
        }, [], connector)
