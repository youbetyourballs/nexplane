# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

#!/usr/bin/env python3
"""
watchdog22: MAC_AGENT_BOOTSTRAP via runner + SANTA_SYNC local.
- Retries Phase 1 automatically on InsufficientCapacityOnHost (up to 6h)
- SSM key stored as String (not SecureString) — works from runner IAM role
- Existing instance NOT terminated on SSM key failure (raises instead)
- Subprocess timeout 22000s > run_on_ec2 18000s poller + setup time
"""
import subprocess, sys, time, datetime

LOG = "/tmp/watchdog22_out.log"
RETRY_INTERVAL = 600  # 10 min between retries on InsufficientCapacity
MAX_RETRIES = 36      # up to 6 hours


def log(msg):
    ts = datetime.datetime.utcnow().strftime('%H:%M:%S')
    line = f'[{ts}] {msg}'
    print(line, flush=True)
    with open(LOG, 'a') as f:
        f.write(line + '\n')


log('=== watchdog22 start ===')

log('Phase 1: MAC_AGENT_BOOTSTRAP via run_on_ec2.py')
r1 = None
for attempt in range(1, MAX_RETRIES + 1):
    r1 = subprocess.run(
        ['docker', 'exec', 'nexplane-backend-1',
         'python3', 'tests/smoke/run_on_ec2.py',
         '--phases', 'MAC_AGENT_BOOTSTRAP',
         '--region', 'us-east-1',
         '--dedicated-host-id', 'h-0d03a30df9b884c06'],
        capture_output=True, text=True, timeout=22000,
    )
    with open('/tmp/watchdog22_phase1_stdout.log', 'w') as f:
        f.write(r1.stdout)
    with open('/tmp/watchdog22_phase1_stderr.log', 'w') as f:
        f.write(r1.stderr)

    if r1.returncode == 0:
        log(f'Phase 1 PASSED on attempt {attempt}')
        break

    combined = r1.stdout + r1.stderr
    if 'InsufficientCapacityOnHost' in combined:
        log(f'Phase 1 attempt {attempt}: InsufficientCapacityOnHost — retrying in {RETRY_INTERVAL}s...')
        time.sleep(RETRY_INTERVAL)
        continue

    # Any other failure — log and abort
    log(f'Phase 1 FAILED (attempt {attempt}, exit={r1.returncode})')
    log(f'Phase 1 stdout tail:\n{r1.stdout[-3000:]}')
    if r1.stderr:
        log(f'Phase 1 stderr tail: {r1.stderr[-500:]}')
    sys.exit(1)
else:
    log(f'Phase 1 FAILED: InsufficientCapacityOnHost after {MAX_RETRIES} attempts')
    sys.exit(1)

log(f'Phase 1 exit={r1.returncode}')
log(f'Phase 1 stdout tail:\n{r1.stdout[-3000:]}')
if r1.stderr:
    log(f'Phase 1 stderr tail: {r1.stderr[-500:]}')

log('Phase 1 done -- waiting 30s for backend stability before Phase 2')
time.sleep(30)

log('Phase 2: SANTA_SYNC local in platform container')
r2 = subprocess.run(
    ['docker', 'exec', 'nexplane-backend-1',
     'python3', 'tests/smoke/test_aws_live.py',
     '--phases', 'SANTA_SYNC',
     '--local'],
    capture_output=True, text=True, timeout=600,
)
log(f'Phase 2 exit={r2.returncode}')
with open('/tmp/watchdog22_phase2_stdout.log', 'w') as f:
    f.write(r2.stdout)
with open('/tmp/watchdog22_phase2_stderr.log', 'w') as f:
    f.write(r2.stderr)
log(f'Phase 2 stdout tail:\n{r2.stdout[-3000:]}')
if r2.stderr:
    log(f'Phase 2 stderr tail: {r2.stderr[-500:]}')

if r2.returncode == 0:
    log('=== ALL PHASES PASSED ===')
    open('/tmp/smoke22_done', 'w').write('PASS')
else:
    log('Phase 2 FAILED')
    sys.exit(1)
