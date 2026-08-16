# Task 3: BIND DNS DNSSEC executor (cross-cloud DNSSEC parity project)

## Status: DONE

## Commits
- `f4b41bf` — feat: add bind_dns_dnssec_sign_zone executor and catalog entry

## Test Summary
2/2 passed in 0.26s (`backend/app/tests/executors/test_bind_dns_dnssec_sign_zone.py`)
- test_signs_zone: PASSED
- test_rollback_restores_unsigned_zone: PASSED

## Files Created/Modified
- `backend/app/connectors/executors/bind_dns/bind_dns_dnssec_sign_zone.py` — DNSSEC signing executor: KSK/ZSK generation, zone signing, named.conf update, rndc reload, full rollback
- `backend/app/tests/executors/test_bind_dns_dnssec_sign_zone.py` — 2 unit tests covering signing and rollback scenarios
- `backend/app/connectors/catalog/bind_dns.json` — appended bind_dns_dnssec_sign_zone action entry

## Concerns
None.

---

# Task 3 Report: Container Image Transfer Executor

## Status: DONE

## Commits
- `35eb8e3` — feat(image-transfer): implement container_image_transfer executor with rollback

## Test Summary
12/12 passed (`backend/tests/unit/test_container_image_transfer.py`)

## Files Created/Modified
- `backend/app/connectors/executors/container_image_transfer.py` — full executor: mock path, 5-phase pipeline, ACR import, agent docker transfer, rollback
- `backend/tests/unit/test_container_image_transfer.py` — added 5 new tests (execute mock, preflight x2, rollback x2); total 12 tests

## Concerns
None.

---

# (Previous occupant of this file: GCP Account Baseline Monitoring)

## Status: DONE

## Commits
- `ae63517` — feat(baseline): GCP account baseline monitoring executor + catalog

## Test Summary
4/4 passed in 0.23s (`tests/unit/test_gcp_account_baseline_monitoring.py`)

## Files Created/Modified
- `backend/app/connectors/executors/gcp/gcp_account_baseline_monitoring.py` — executor with 5 phases, ROLLBACK_CAPABILITY = "full", mock path, SCC org-level caveat, FILO rollback
- `backend/app/connectors/change_type_definitions/gcp_account_baseline_monitoring.json` — change type definition
- `backend/app/connectors/catalog/gcp.json` — appended gcp_account_baseline_monitoring action entry
- `backend/tests/unit/test_gcp_account_baseline_monitoring.py` — 4 unit tests

## Concerns
None.

---

## Fix Pass — 2026-08-02

### Commit
`255aae6` — fix(baseline): correct GCP audit log type, SCC labeling, DNS snapshot field

### Changes Made

1. **Fix 1 — Audit log type (line ~120):** `ADMIN_READ` → `ADMIN_WRITE`. `ADMIN_READ` is not a valid GCP audit log type; `ADMIN_WRITE` is required to enable Admin Activity logging.

2. **Fix 2 — SCC labeling:** `action: "enabled"` → `action: "asset_discovery_enabled"` with a note that Standard tier requires manual upgrade in GCP Console. The v1 API only supports `enableAssetDiscovery`; claiming "Standard tier enabled" was inaccurate.

3. **Fix 3 — DNS snapshot field:** Replaced `z.get("privateVisibilityConfig", {}).get("enableLogging", False)` with `False` for all private zones. DNS query logging is controlled by `dnsPolicy` resources linked to networks, not a field on the zone object. Recording `False` as the pre-existing state is safe: the enable call is attempted for all zones, and rollback will disable any zones we enabled.

4. **Fix 4 — asyncio deprecation:** `asyncio.get_event_loop().run_in_executor(...)` → `asyncio.get_running_loop().run_in_executor(...)`.

### Test Results
4 passed in 0.22s (`tests/unit/test_gcp_account_baseline_monitoring.py`)

---

## Fix Pass 2 — 2026-08-02

### Commit
`8604a87` — fix(baseline): skip GCP DNS logging with warning; correct API not available per-zone

### Changes Made

1. **Enable phase — DNS logging:** Removed the incorrect `managedZones().patch()` call with `{"privateVisibilityConfig": {"enableLogging": True}}` (field does not exist on zone resources). Replaced with a `skipped_with_warning` entry explaining that GCP DNS query logging requires creating `dns.policies` resources linked to VPC networks, which is out of scope for the per-zone approach. Operators should use the GCP Console or a dedicated DNS policy CR.

2. **Rollback phase — DNS logging:** The `dns_logging` `elif` branch in rollback was also calling `managedZones().patch()` with the same invalid field. Replaced with a no-op handler (`nothing_to_undo`) since nothing is enabled, and `continue` to skip the `undone.append` at the end of the loop body.

3. **No test changes needed:** All 4 existing unit tests passed without modification; none tested the live DNS enable path.

### Test Results
4 passed in 0.22s (`tests/unit/test_gcp_account_baseline_monitoring.py`)

---

## Fix Round 1 — container_image_transfer.py

### Changes Made

1. **Issue #1 (Critical) — Shell injection in `_transfer_via_agent`:** Replaced inline single-quoted passwords in the `echo '...' | docker login` commands with `$SRC_PASS` / `$DST_PASS` environment variable references. Passwords are now passed via the `env` dict in `dispatch_agent_job` parameters instead of being interpolated into the shell script string.

2. **Issue #2 (Important) — Blocking `time.sleep` in ACR import polling:** Split `_transfer_acr_import`'s single `_do()` closure into two parts: `_do_post()` (runs in executor, makes the initial POST and returns the raw response + `mgmt_token`), and `_do_poll()` (per-iteration closure, runs in executor). The polling loop now lives in the async function body and uses `await asyncio.sleep(5)` between polls, eliminating thread-pool starvation.

3. **Issue #3 (Important) — Wrong OCIR username for ACR import source:** Replaced the two-branch `"AWS" if aws else "oauth2accesstoken"` with a four-branch block: `aws→"AWS"`, `gcp→"oauth2accesstoken"`, `oci→"{tenancy_namespace}/{username}"`, else `"token"`.

### Test Command
```
cd backend && python -m pytest tests/unit/test_container_image_transfer.py -v
```

### Test Output
12/12 passed in 5.31s

---

## Fix Pass — bind_dns_dnssec_sign_zone.py — 2026-08-16

### Commit
`d2d7985` — fix: bind_dns_dnssec_sign_zone — sanitize key names, drop invalid -b flag for ECDSAP256SHA256

### Changes Made

1. **Finding 1 — Shell injection via unsanitized ksk_name/zsk_name:** Key names parsed from `dnssec-keygen` output were interpolated directly into the `dnssec-signzone` command without sanitization. Fixed by parsing raw output, then applying `re.sub(r"[^A-Za-z0-9._+\-]", "", raw_name)` to both `ksk_name` and `zsk_name` before use in shell commands.

2. **Finding 2 — Invalid `-b 256` flag for ECDSAP256SHA256:** The `-b` (key size) flag is not valid with ECDSAP256SHA256 (fixed 256-bit curves). Removed `-b 256` from both KSK and ZSK `dnssec-keygen` invocations:
   - KSK: `dnssec-keygen -a ECDSAP256SHA256 -n ZONE -f KSK {zone}`
   - ZSK: `dnssec-keygen -a ECDSAP256SHA256 -n ZONE {zone}`

### Test Results
2/2 passed in 0.19s (`backend/app/tests/executors/test_bind_dns_dnssec_sign_zone.py`)
