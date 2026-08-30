# Task 3 Report: Catalog Entry + Smoke Test

**Status:** COMPLETE

## Commits Made

- `720908a` — feat: add kernel_upgrade catalog entry and smoke test (smoke_verified=false pending live run)
  - `backend/app/connectors/catalog/nexplane_agent.json` — kernel_upgrade action added after agent_os_upgrade
  - `backend/app/tests/executors/test_kernel_upgrade_smoke.py` — new file, 3 phases

## Test Summary

Unit tests: **6 passed** (test_kernel_upgrade.py, unchanged). Smoke test: **3 skipped** when SMOKE_KERNEL_ASSET_ID not set (correct guard behavior).

## Concerns

- SCP subsystem issue on this host requires `-O` flag (legacy SCP protocol). Standard `scp` fails with "subsystem request failed"; `-O` works fine.
- `smoke_verified` remains `false` — must flip to `true` only after live smoke run completes (requires a registered AL2023 EC2 asset with nexplane agent installed and SMOKE_KERNEL_ASSET_ID set).
