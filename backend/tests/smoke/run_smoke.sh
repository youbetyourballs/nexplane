#!/usr/bin/env bash
# Helper script to run smoke tests from inside the backend Docker container.
# Avoids Docker Desktop 9P filesystem D-state issues by:
# 1. Pre-generating tarball in foreground (bind-mount-heavy step)
# 2. Copying run_on_ec2.py to /tmp (avoids bind-mount reads in background)
# 3. Fetching credentials in foreground
# 4. Launching the actual run in background from /tmp
#
# Usage:
#   docker exec nexplane-backend-1 bash /app/tests/smoke/run_smoke.sh WAZUH_AGENT,LDAP_ROTATE
#   docker exec nexplane-backend-1 bash /app/tests/smoke/run_smoke.sh WINRM_BOOTSTRAP --keep-runner

set -e

PHASES="${1:-LDAP_ROTATE}"
EXTRA_ARGS="${@:2}"
_PHASES_SLUG="${PHASES//,/_}"
if [ ${#_PHASES_SLUG} -gt 180 ]; then
  _PHASES_SLUG="$(echo -n "$PHASES" | md5sum | cut -c1-12)_multi"
fi
LOG="/tmp/smoke_run_${_PHASES_SLUG}.log"
DONE="/tmp/smoke_run_${_PHASES_SLUG}_done"

echo "[run_smoke.sh] Phases: $PHASES"
echo "[run_smoke.sh] Log: $LOG"

# Step 1: Fetch credentials
echo "[run_smoke.sh] Fetching credentials..."
cd /app
python3 -c "
import asyncio, sys
sys.path.insert(0,'/app')
from app.config import settings
from app.services.secret_backend_factory import get_secret_backend
from app.models.connector import Connector
from app.models.connector_credential import ConnectorCredential
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

async def get():
    e = create_async_engine(settings.DATABASE_URL, pool_pre_ping=False)
    S = sessionmaker(e, class_=AsyncSession, expire_on_commit=False)
    async with S() as db:
        backend = get_secret_backend()
        r = await db.execute(select(Connector).where(Connector.connector_type == 'aws'))
        c = r.scalar_one_or_none()
        cr = await db.execute(select(ConnectorCredential).where(ConnectorCredential.connector_id == c.id))
        cc = cr.scalar_one_or_none()
        aws = backend.decrypt_json(cc.credentials_encrypted)
        r2 = await db.execute(select(Connector).where(Connector.connector_type == 'tailscale'))
        c2 = r2.scalar_one_or_none()
        cr2 = await db.execute(select(ConnectorCredential).where(ConnectorCredential.connector_id == c2.id))
        cc2 = cr2.scalar_one_or_none()
        ts = backend.decrypt_json(cc2.credentials_encrypted)
        await e.dispose()
        return aws, ts

aws, ts = asyncio.run(get())
print(f'AWS_ACCESS_KEY_ID={aws[\"access_key_id\"]}')
print(f'AWS_SECRET_ACCESS_KEY={aws[\"secret_access_key\"]}')
print(f'AWS_DEFAULT_REGION={aws.get(\"region\",\"us-east-1\")}')
print(f'TAILSCALE_AUTH_KEY={ts.get(\"auth_key\",\"\")}')
" > /tmp/smoke_creds.env
echo "[run_smoke.sh] Credentials fetched"

# Step 2: Pre-generate tarball
echo "[run_smoke.sh] Pre-generating tarball..."
cd /app/tests/smoke
python3 -c "
import sys; sys.path.insert(0, '/app/tests/smoke')
from run_on_ec2 import make_test_tarball
tarball = make_test_tarball()
with open('/tmp/smoke_tarball.tar.gz', 'wb') as f:
    f.write(tarball)
print(f'Tarball: {len(tarball)//1024}KB')
"
echo "[run_smoke.sh] Tarball ready"

# Step 3: Copy run_on_ec2.py to /tmp to avoid bind-mount reads in background
cp /app/tests/smoke/run_on_ec2.py /tmp/run_on_ec2_launch.py

# Step 4: Launch from /tmp with all necessary args
echo "[run_smoke.sh] Launching run..."
source /tmp/smoke_creds.env
rm -f "$LOG" "$DONE"

python3 -u /tmp/run_on_ec2_launch.py \
  --phases "$PHASES" \
  --tarball-path /tmp/smoke_tarball.tar.gz \
  --tailscale-auth-key "$TAILSCALE_AUTH_KEY" \
  $EXTRA_ARGS \
  > "$LOG" 2>&1

echo "[run_smoke.sh] Run completed. Exit: $?"
touch "$DONE"
