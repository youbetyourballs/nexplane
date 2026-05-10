# OCI Connector — Sub-project 6: Full Smoke Test Suite + UI Verification Design

## Scope

Complete smoke test coverage for all OCI sub-projects (1-5), run after implementation of all sub-projects. Includes debugging any failures, verifying asset inventory population, and confirming all UI tabs (Assets, Change Requests, Connectors, Compliance) reflect OCI resources correctly.

This sub-project is the integration and validation layer. It does not add new executors or change types — it verifies everything works end-to-end against the real OCI tenancy.

---

## Test Structure

All OCI phases run as a single track in `test_multicloud_live.py`, integrated with the existing AWS/GCP/Azure parallel tracks. Phases are independent (each cleans up after itself) so individual phases can be re-run to debug failures.

```
OCI_A  Sub-project 1: Compartment discovery + VCN + subnet setup
OCI_B  Sub-project 1: Instance launch + Tailscale + agent deploy
OCI_C  Sub-project 1: Instance lifecycle (stop/start/reboot)
OCI_D  Sub-project 1: Block volume snapshot
OCI_E  Sub-project 1: Teardown (terminate instance + delete VCN)
OCI_F  Sub-project 2: Object Storage (create/lifecycle/block-public/delete)
OCI_G  Sub-project 2: Block Volumes (create/attach/detach/backup/delete)
OCI_H  Sub-project 3: Security Lists + NSGs
OCI_I  Sub-project 3: Load Balancer (create/backend-set/listener/delete)
OCI_J  Sub-project 3: DNS (zone + record)
OCI_K  Sub-project 4: IAM (user/group/policy create + disable/enable + delete)
OCI_L  Sub-project 4: Vault secrets (conditional — skipped if no vault exists)
OCI_M  Sub-project 5: Autonomous Database (create/stop/start/backup/delete)
OCI_N  Sub-project 5: Monitoring alarms + Logging
```

Default phases run overnight: `OCI_A,OCI_B,OCI_C,OCI_D,OCI_E,OCI_F,OCI_G,OCI_H,OCI_J,OCI_K,OCI_N`

Slow/conditional phases (opt-in): `OCI_I` (LB ~15 min), `OCI_L` (Vault, requires vault resource), `OCI_M` (ADB ~15 min)

---

## Shared Test Infrastructure

### `_get_oci_creds()`
Reads `oci` connector credentials from DB via `SecretsService` (same pattern as `_get_aws_boto3_client`). Returns OCI SDK config dict for side-effect verification calls.

### `_get_oci_compute_client()`, `_get_oci_identity_client()`, etc.
Thin wrappers that call `_get_oci_creds()` and return typed OCI SDK clients. Used for all SDK-side verification (not for Nexplane CRs — those use the standard `client.run_cr()` pattern).

### Credential sourcing
```python
# Pattern: read connector creds from Nexplane DB
oci_creds = _get_oci_creds(client)  # reads via /connectors/{id}/credentials
compute = oci.core.ComputeClient(oci_creds)
```

---

## Assertions Pattern

Each phase follows the standard smoke test assertion pattern:
1. Fire Nexplane CR via `client.run_cr()` or `client._run_cr_with_timeout()`
2. Assert CR reaches `completed` status
3. Assert `_auto_asset` created in Nexplane inventory: `client.get("/assets", params={"q": name})`
4. Assert OCI SDK side-effect: call OCI SDK directly and verify resource state
5. Assert asset metadata contains expected fields (instance_id, compartment_id, etc.)

### New assertion helper: `_assert_oci_asset_metadata()`
```python
def _assert_oci_asset_metadata(client, asset_name: str, expected_keys: list[str]) -> dict:
    """Verify an OCI asset exists in inventory and has required metadata keys."""
    assets = client.get("/assets", params={"q": asset_name})
    asset = next((a for a in assets if a["name"] == asset_name), None)
    if not asset:
        fail(f"OCI asset '{asset_name}' not found in inventory")
    for key in expected_keys:
        if key not in (asset.get("asset_metadata") or {}):
            fail(f"OCI asset '{asset_name}' missing metadata key '{key}'")
    return asset
```

---

## UI Verification Checklist

After each phase group, the smoke test runner logs which UI surfaces to verify manually. These are noted in phase output (not automated — requires human spot-check during the debugging session):

### Assets tab
- [ ] OCI compartments appear as `cloud_account` assets filtered by `connector_type=oci`
- [ ] OCI instances appear as `server` assets with correct shape + IP metadata
- [ ] OCI buckets appear as `storage_bucket` assets
- [ ] OCI block volumes appear as `storage_bucket` assets tagged `oci-block-volume`
- [ ] VCNs/subnets appear as `application` assets tagged `oci-vcn`/`oci-subnet`
- [ ] IAM users appear as `identity` assets
- [ ] ADB appears as `database` asset with connection_strings in metadata

### Change Requests tab
- [ ] All OCI CRs appear with correct change_type labels
- [ ] CR detail shows plan steps, execution result, rollback result
- [ ] AI badge appears on `oci_instance_create`

### Connectors tab
- [ ] OCI connector appears with status `active`
- [ ] "Test Connection" calls `identity.get_tenancy()` and returns success
- [ ] "Run Ingest" triggers compartment + instance + VCN discovery

### Create Change Request modal
- [ ] "Oracle Cloud" category visible
- [ ] All 8+ OCI change types listed with descriptions
- [ ] Outcome templates pre-populated with correct defaults

### Asset Detail pages
- [ ] Clicking an OCI `server` asset shows structured metadata panel (instance_id, shape, IP)
- [ ] Clicking an OCI `cloud_account` (compartment) shows compartment_id, region, provider
- [ ] Quick actions present for each OCI asset type

---

## Failure Debugging Protocol

The smoke test runner (in the UI and CLI) already supports live log streaming. For OCI failures:

1. **CR status `failed`**: fetch `execution_runs[0].result.error` → usually an OCI SDK exception with clear message
2. **Quota limits**: new OCI accounts have limits (e.g., 2 ADB instances, 4 ARM OCPUs). If quota exceeded, test skips with warning.
3. **Slow operations**: LB and ADB creation can take 15+ min. Timeouts set to 20 min for these phases.
4. **Asset not appearing in inventory**: check `connector_id` on asset — OCI assets must be linked to the OCI connector, not null.
5. **Mock return paths**: if creds aren't flowing to executor, result will have `"mock": true` — check SecretsService decryption.

---

## `--phases` Help String Update

The phases help string in `test_multicloud_live.py` is updated to include:
```
OCI_A-E: OCI compute+VCN lifecycle (sub-project 1)
OCI_F-G: OCI storage (sub-project 2)
OCI_H-J: OCI networking (sub-project 3)
OCI_K-L: OCI identity+vault (sub-project 4)
OCI_M-N: OCI database+observability (sub-project 5)
```

---

## Smoke Test Runner UI

The existing Smoke Tests page in the Nexplane UI already supports streaming logs per phase. No UI changes needed for OCI phases — they appear automatically once added to `test_multicloud_live.py`.

The "OCI" track label is added alongside "AWS", "GCP", "Azure" in the suite card description.

---

## Post-Smoke-Test Verification Steps

After the overnight run, verify:

1. **No orphaned OCI resources**: check OCI Console for any resources created by smoke tests that weren't cleaned up. Each phase's rollback/teardown should leave zero residual resources.
2. **Inventory accuracy**: run a manual ingest via the Connectors tab and verify asset counts match what's in the OCI Console.
3. **Credentials still valid**: OCI API keys don't expire automatically, but verify the connector test returns success.
4. **Always Free limits intact**: confirm ADB (1 instance, 20 GB) and compute (VM.Standard.E2.1.Micro, 1 instance) free limits weren't permanently consumed (teardown should release them).

---

## Backlog Note

After sub-project 6 passes, update the memory file:
- Mark `Oracle Cloud Infrastructure (OCI) connector -- full feature parity` as **completed**
- Note the connector_id and tenancy for reference
- Note any OCI-specific quirks discovered during debugging (slow LB provisioning, ADB name length limit, etc.)
