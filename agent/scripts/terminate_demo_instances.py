"""Terminate stale nexplane-demo-payments EC2 instances using connector creds."""
import asyncio, boto3, sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from app.config import settings
from app.models.connector import Connector, ConnectorType
from app.services.connector_service import _attach_credentials


async def main():
    engine = create_async_engine(settings.DATABASE_URL)
    S = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with S() as db:
        r = await db.execute(sa.select(Connector).where(Connector.connector_type == ConnectorType.aws))
        conn = r.scalars().first()
        await _attach_credentials(conn, db)
        creds = getattr(conn, "credentials", {})
    await engine.dispose()
    ec2 = boto3.client("ec2", region_name="us-east-1",
        aws_access_key_id=creds["access_key_id"],
        aws_secret_access_key=creds["secret_access_key"])
    result = ec2.describe_instances(Filters=[
        {"Name": "tag:Name", "Values": ["nexplane-demo-payments"]},
        {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]},
    ])
    for res in result["Reservations"]:
        for inst in res["Instances"]:
            iid = inst["InstanceId"]
            ec2.terminate_instances(InstanceIds=[iid])
            print(f"Terminated {iid}")
    if not result["Reservations"]:
        print("No stale instances found")

asyncio.run(main())
