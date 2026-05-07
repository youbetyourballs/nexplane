import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    db_id = parameters.get('db_instance_identifier', 'nexplane-db')
    engine = parameters.get('engine', 'mysql')
    engine_version = parameters.get('engine_version', '8.0')
    instance_class = parameters.get('db_instance_class', 'db.t3.micro')
    username = parameters.get('master_username', 'admin')
    password = parameters.get('master_password', 'Nexplane!Smoke1')
    storage = parameters.get('allocated_storage', 20)
    skip_final = parameters.get('skip_final_snapshot', True)

    if not creds:
        return {
            "action": "create_rds_instance",
            "db_instance_identifier": db_id,
            "endpoint": "mock-rds.rds.amazonaws.com",
            "port": 3306,
            "mock": True,
            "_auto_asset": {
                "name": db_id,
                "asset_type": "database",
                "environment": "prod",
                "criticality": "high",
                "asset_metadata": {"db_instance_identifier": db_id, "engine": engine, "provider": "aws"},
                "tags": ["rds", "nexplane-managed"],
            },
        }

    from ._client import get_boto3_client
    rds = get_boto3_client(creds, 'rds')
    loop = asyncio.get_event_loop()

    def _call():
        rds.create_db_instance(
            DBInstanceIdentifier=db_id,
            DBInstanceClass=instance_class,
            Engine=engine,
            EngineVersion=engine_version,
            MasterUsername=username,
            MasterUserPassword=password,
            AllocatedStorage=storage,
            PubliclyAccessible=False,
            BackupRetentionPeriod=0,
            Tags=[{"Key": "ManagedBy", "Value": "nexplane"}],
        )
        waiter = rds.get_waiter('db_instance_available')
        waiter.wait(
            DBInstanceIdentifier=db_id,
            WaiterConfig={"Delay": 30, "MaxAttempts": 40},
        )
        info = rds.describe_db_instances(DBInstanceIdentifier=db_id)['DBInstances'][0]
        ep = info.get('Endpoint', {})
        return ep.get('Address', ''), ep.get('Port', 3306)

    endpoint, port = await loop.run_in_executor(None, _call)
    return {
        "action": "create_rds_instance",
        "db_instance_identifier": db_id,
        "endpoint": endpoint,
        "port": port,
        "engine": engine,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_auto_asset": {
            "name": db_id,
            "asset_type": "database",
            "environment": "prod",
            "criticality": "high",
            "asset_metadata": {
                "db_instance_identifier": db_id,
                "engine": engine,
                "endpoint": endpoint,
                "port": port,
                "region": creds.get('region', 'us-east-1'),
                "provider": "aws",
            },
            "tags": ["rds", "nexplane-managed"],
        },
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.aws.delete_rds_instance import execute as delete
    return await delete(
        {"db_instance_identifier": execution_result.get('db_instance_identifier', parameters.get('db_instance_identifier'))},
        [], connector,
    )
