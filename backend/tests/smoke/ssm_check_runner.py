import asyncio, sys, time
sys.path.insert(0, '/app')
from app.config import settings
from app.services.secrets_service import SecretsService
from app.models.connector import Connector
from app.models.connector_credential import ConnectorCredential
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
import boto3

RUNNER_ID = 'i-01e71c69a5c38c6f9'

async def main():
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=False)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as db:
        row = await db.execute(select(Connector).where(Connector.connector_type == 'aws'))
        conn = row.scalar_one_or_none()
        cred_row = await db.execute(select(ConnectorCredential).where(ConnectorCredential.connector_id == conn.id))
        cred = cred_row.scalar_one_or_none()
        svc = SecretsService(settings.SECRET_KEY)
        aws = svc.decrypt_json(cred.credentials_encrypted)
        ssm = boto3.client('ssm', region_name='us-east-1',
            aws_access_key_id=aws['access_key_id'],
            aws_secret_access_key=aws['secret_access_key'])
        r = ssm.send_command(
            InstanceIds=[RUNNER_ID],
            DocumentName='AWS-RunShellScript',
            Parameters={'commands': [
                'ps aux | grep -E "python|smoke|run_on" | grep -v grep | head -10',
                'echo ---LOGS---',
                'ls /tmp/*.log 2>/dev/null',
                'tail -80 /tmp/smoke_run_MAC*.log 2>/dev/null || tail -80 /tmp/run_on_ec2*.log 2>/dev/null || echo no_smoke_log',
            ]}
        )
        cmd_id = r['Command']['CommandId']
        print('sent cmd:', cmd_id)
        for _ in range(12):
            time.sleep(5)
            out = ssm.get_command_invocation(CommandId=cmd_id, InstanceId=RUNNER_ID)
            if out['Status'] not in ('Pending', 'InProgress'):
                break
        print('status:', out['Status'])
        print('STDOUT:', out['StandardOutputContent'][-4000:])
        if out['StandardErrorContent'].strip():
            print('STDERR:', out['StandardErrorContent'][-500:])

asyncio.run(main())
