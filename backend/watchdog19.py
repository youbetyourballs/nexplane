#!/usr/bin/env python3
"""
watchdog19: MAC_AGENT_BOOTSTRAP via runner + SANTA_SYNC local.
SSH key now persists to SSM so instance reuse works across runner restarts.
"""
import subprocess, sys, time, datetime

LOG = "/tmp/watchdog19_out.log"


def log(msg):
    ts = datetime.datetime.utcnow().strftime('%H:%M:%S')
    line = f'[{ts}] {msg}'
    print(line, flush=True)
    with open(LOG, 'a') as f:
        f.write(line + '\n')


log('=== watchdog19 start ===')

# Phase 1: MAC_AGENT_BOOTSTRAP via EC2 runner (5-hour timeout to cover scrub cycle)
log('Phase 1: MAC_AGENT_BOOTSTRAP via run_on_ec2.py')
r1 = subprocess.run(
    ['docker', 'exec', 'nexplane-backend-1',
     'python3', 'tests/smoke/run_on_ec2.py',
     '--phases', 'MAC_AGENT_BOOTSTRAP',
     '--region', 'us-east-1',
     '--dedicated-host-id', 'h-0d03a30df9b884c06'],
    capture_output=True, text=True, timeout=18000,
)
log(f'Phase 1 exit={r1.returncode}')
with open('/tmp/watchdog19_phase1_stdout.log', 'w') as f:
    f.write(r1.stdout)
with open('/tmp/watchdog19_phase1_stderr.log', 'w') as f:
    f.write(r1.stderr)
log(f'Phase 1 stdout tail: {r1.stdout[-2000:]}')
if r1.stderr:
    log(f'Phase 1 stderr tail: {r1.stderr[-500:]}')

if r1.returncode != 0:
    log('Phase 1 FAILED -- aborting')
    sys.exit(1)

# Brief pause to ensure backend container is healthy before Phase 2
log('Phase 1 done -- waiting 30s for backend stability before Phase 2')
time.sleep(30)

# Phase 2: SANTA_SYNC locally in platform container (mock server on container localhost)
log('Phase 2: SANTA_SYNC local in platform container')
r2 = subprocess.run(
    ['docker', 'exec', 'nexplane-backend-1',
     'python3', 'tests/smoke/test_aws_live.py',
     '--phases', 'SANTA_SYNC',
     '--local'],
    capture_output=True, text=True, timeout=600,
)
log(f'Phase 2 exit={r2.returncode}')
with open('/tmp/watchdog19_phase2_stdout.log', 'w') as f:
    f.write(r2.stdout)
with open('/tmp/watchdog19_phase2_stderr.log', 'w') as f:
    f.write(r2.stderr)
log(f'Phase 2 stdout tail: {r2.stdout[-2000:]}')
if r2.stderr:
    log(f'Phase 2 stderr tail: {r2.stderr[-500:]}')

if r2.returncode == 0:
    log('=== ALL PHASES PASSED ===')
    open('/tmp/smoke19_done', 'w').write('PASS')
else:
    log('Phase 2 FAILED')
    sys.exit(1)
