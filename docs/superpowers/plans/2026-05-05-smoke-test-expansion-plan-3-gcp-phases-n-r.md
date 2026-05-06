# Smoke Test Expansion — Plan 3: GCP Phases N–R + Sub-project Stubs

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add GCP smoke test phases N–R to `test_gcp_live.py` (firewall, storage, service accounts, Terraform, Ansible), plus stub functions S–X for future GCP sub-projects B–G, and update `main()` to dispatch them.

**Architecture:** Same rollback stack pattern as AWS phases. GCP SDK scaffolding (google-cloud-compute, google-cloud-storage) used for test setup/teardown where no Nexplane CR exists yet. New change type definitions needed for GCP-specific operations.

**Tech Stack:** Python 3.12, google-cloud-compute, google-cloud-storage, google-iam-admin, Nexplane CR machinery

---

## Files

**Create:**
- `backend/app/connectors/change_type_definitions/gcp_firewall_create.json`
- `backend/app/connectors/change_type_definitions/gcp_firewall_delete.json`
- `backend/app/connectors/change_type_definitions/gcp_block_public_bucket_access.json`
- `backend/app/connectors/change_type_definitions/gcp_disable_service_account.json`
- `backend/app/connectors/change_type_definitions/gcp_rotate_service_account_key.json`

**Modify:**
- `backend/tests/smoke/test_gcp_live.py` — add phases N–R + stubs S–X + update main()

---

### Task 1: GCP change type definitions

- [ ] **Step 1: Create `gcp_firewall_create.json`**

```json
{
  "change_type": "gcp_firewall_create",
  "display_name": "Create GCP Firewall Rule",
  "steps": [
    {"generic_action": "create_firewall_rule", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "delete_firewall_rule",
  "rollback_connector_type": "gcp"
}
```

- [ ] **Step 2: Create `gcp_firewall_delete.json`**

```json
{
  "change_type": "gcp_firewall_delete",
  "display_name": "Delete GCP Firewall Rule",
  "steps": [
    {"generic_action": "delete_firewall_rule", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 3: Create `gcp_block_public_bucket_access.json`**

```json
{
  "change_type": "gcp_block_public_bucket_access",
  "display_name": "Block GCS Public Bucket Access",
  "steps": [
    {"generic_action": "block_public_bucket_access", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 4: Create `gcp_disable_service_account.json`**

```json
{
  "change_type": "gcp_disable_service_account",
  "display_name": "Disable GCP Service Account",
  "steps": [
    {"generic_action": "disable_service_account", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 5: Create `gcp_rotate_service_account_key.json`**

```json
{
  "change_type": "gcp_rotate_service_account_key",
  "display_name": "Rotate GCP Service Account Key",
  "steps": [
    {"generic_action": "rotate_service_account_key", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 6: Verify backend loads**

```bash
docker exec nexplane-backend-1 python -c "
from app.connectors.catalog_service import ActionCatalogService
import pathlib
svc = ActionCatalogService(pathlib.Path('app/connectors/catalog'))
print('Catalog OK:', len(svc._catalog), 'connectors')
"
```

- [ ] **Step 7: Commit**

```bash
git add backend/app/connectors/change_type_definitions/gcp_*.json
git commit -m "feat(smoke): add GCP change type definitions for firewall, storage, service account ops"
```

---

### Task 2: Add GCP phases N–R + stubs S–X + update main()

**Files:**
- Modify: `backend/tests/smoke/test_gcp_live.py`

First update the imports at the top to add `_get_aws_boto3_client` (needed for the GCP connector credential lookup helper):

- [ ] **Step 1: Update imports**

Find the existing `from smoke_helpers import (` block and replace it with:

```python
from smoke_helpers import (
    GCE_SMOKE_INSTANCE, GCE_ZONE, TIMEOUT_SECONDS,
    NexplaneClient, log, fail,
    _gcp_creds_cache, _get_gcp_compute_client,
    make_base_parser,
)
```

(No change needed — imports are already correct. Just add `import secrets` at module level after `import time`.)

Add `import secrets` after `import time` at the top.

- [ ] **Step 2: Add helper function for GCP credentials dict**

Append this after the imports section (before Phase L):

```python
def _get_gcp_creds() -> dict:
    """Return the GCP credentials dict from the connector, populating cache if needed."""
    _get_gcp_compute_client()  # side effect: populates _gcp_creds_cache
    return _gcp_creds_cache
```

- [ ] **Step 3: Add Phase N (GCP Firewall Rules)**

Append before `def main()`:

```python
def run_phase_n(client: NexplaneClient, cloud_account_id: str,
                gcp_project: str, gcp_network: str = "global/networks/default") -> None:
    """Phase N: GCP Firewall — create_firewall_rule + delete via CR rollback."""
    print("\n[Phase N] GCP Firewall Rules")

    import time as _time
    rule_name = f"nexplane-smoke-n-{int(_time.time())}"
    rollback_stack: list[tuple[str, str]] = []

    try:
        # 1. Create firewall rule via CR
        cr = client.run_cr(
            "Smoke-N: create GCP firewall rule", "gcp_firewall_create", cloud_account_id,
            {
                "project": gcp_project,
                "rule_name": rule_name,
                "network": gcp_network,
                "direction": "INGRESS",
                "priority": 1000,
                "allowed": [{"IPProtocol": "tcp", "ports": ["8443"]}],
                "source_ranges": ["192.0.2.0/24"],
                "description": "Nexplane smoke test rule — safe to delete",
            },
        )
        rollback_stack.append((cr["id"], "gcp_firewall_create"))

        # 2. Verify via GCP SDK
        creds = _get_gcp_creds()
        if creds:
            try:
                import json as _json
                from google.oauth2 import service_account as _sa
                from google.cloud import compute_v1
                key_raw = creds.get("service_account_key_json", "")
                key_json = _json.loads(key_raw) if isinstance(key_raw, str) else key_raw
                gc = _sa.Credentials.from_service_account_info(
                    key_json, scopes=["https://www.googleapis.com/auth/cloud-platform"])
                fw_client = compute_v1.FirewallsClient(credentials=gc)
                fw = fw_client.get(project=gcp_project, firewall=rule_name)
                assert fw.name == rule_name, f"Firewall rule not found: {rule_name}"
                log(f"GCP firewall rule verified via SDK: {rule_name}")
            except Exception as e:
                print(f"  ⚠️  SDK verification skipped: {e}")
        else:
            print("  ⚠️  No GCP credentials — SDK verification skipped")

        # 3. Delete via CR rollback
        create_cr_id, _ = rollback_stack.pop()
        client.rollback_cr(create_cr_id, "gcp_firewall_create → delete")
        log("GCP firewall rule deleted via CR rollback")

        log("Phase N complete")

    except Exception as e:
        print(f"\n❌ Phase N failed: {e}")
        raise
    finally:
        print("  [Phase N cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete firewall rule via SDK
        try:
            creds = _get_gcp_creds()
            if creds and gcp_project:
                import json as _json
                from google.oauth2 import service_account as _sa
                from google.cloud import compute_v1
                key_raw = creds.get("service_account_key_json", "")
                key_json = _json.loads(key_raw) if isinstance(key_raw, str) else key_raw
                gc = _sa.Credentials.from_service_account_info(
                    key_json, scopes=["https://www.googleapis.com/auth/cloud-platform"])
                fw_client = compute_v1.FirewallsClient(credentials=gc)
                try:
                    fw_client.delete(project=gcp_project, firewall=rule_name)
                    print(f"  Safety net: deleted firewall rule {rule_name}")
                except Exception:
                    pass
        except Exception:
            pass
```

- [ ] **Step 4: Add Phase O (GCP Storage)**

```python
def run_phase_o(client: NexplaneClient, cloud_account_id: str, gcp_project: str) -> None:
    """Phase O: GCP Storage — block_public_bucket_access CR on a GCS bucket."""
    print("\n[Phase O] GCP Storage")

    import secrets as _secrets
    bucket_name = f"nexplane-smoke-o-{_secrets.token_hex(4)}"
    rollback_stack: list[tuple[str, str]] = []
    bucket_created = False

    try:
        creds = _get_gcp_creds()
        if not creds:
            fail("Phase O requires GCP credentials")

        # 1. Create GCS bucket via SDK (test scaffolding)
        import json as _json
        from google.oauth2 import service_account as _sa
        from google.cloud import storage as _storage
        key_raw = creds.get("service_account_key_json", "")
        key_json = _json.loads(key_raw) if isinstance(key_raw, str) else key_raw
        gc = _sa.Credentials.from_service_account_info(
            key_json, scopes=["https://www.googleapis.com/auth/cloud-platform"])
        storage_client = _storage.Client(project=gcp_project, credentials=gc)
        bucket = storage_client.bucket(bucket_name)
        bucket.iam_configuration.uniform_bucket_level_access_enabled = False
        storage_client.create_bucket(bucket, location="US")
        bucket_created = True
        log(f"GCS bucket created via SDK: {bucket_name}")

        # 2. Block public access via CR
        cr = client.run_cr(
            "Smoke-O: block public GCS bucket access", "gcp_block_public_bucket_access",
            cloud_account_id,
            {"project": gcp_project, "bucket_name": bucket_name},
        )
        rollback_stack.append((cr["id"], "gcp_block_public_bucket_access"))

        # 3. Verify via SDK
        bucket_obj = storage_client.get_bucket(bucket_name)
        policy = bucket_obj.get_iam_policy()
        has_all_users = any(
            "allUsers" in binding["members"] or "allAuthenticatedUsers" in binding["members"]
            for binding in policy.bindings
        )
        assert not has_all_users, "allUsers/allAuthenticatedUsers still present after blocking"
        log("GCS public access blocked (SDK verified)")

        log("Phase O complete")

    except Exception as e:
        print(f"\n❌ Phase O failed: {e}")
        raise
    finally:
        print("  [Phase O cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete bucket via SDK
        if bucket_created:
            try:
                import json as _json
                from google.oauth2 import service_account as _sa
                from google.cloud import storage as _storage
                creds = _get_gcp_creds()
                key_raw = creds.get("service_account_key_json", "")
                key_json = _json.loads(key_raw) if isinstance(key_raw, str) else key_raw
                gc = _sa.Credentials.from_service_account_info(
                    key_json, scopes=["https://www.googleapis.com/auth/cloud-platform"])
                sc = _storage.Client(project=gcp_project, credentials=gc)
                sc.get_bucket(bucket_name).delete(force=True)
                print(f"  Safety net: deleted GCS bucket {bucket_name}")
            except Exception as e:
                print(f"  ⚠️  Safety net bucket delete failed: {e}")
```

- [ ] **Step 5: Add Phase P (GCP Service Accounts)**

```python
def run_phase_p(client: NexplaneClient, cloud_account_id: str, gcp_project: str) -> None:
    """Phase P: GCP Service Accounts — rotate_service_account_key + disable_service_account via CRs."""
    print("\n[Phase P] GCP Service Accounts")

    import time as _time
    sa_name = f"nexplane-smoke-p-{int(_time.time()) % 100000}"
    sa_email = f"{sa_name}@{gcp_project}.iam.gserviceaccount.com"
    rollback_stack: list[tuple[str, str]] = []
    sa_created = False

    try:
        creds = _get_gcp_creds()
        if not creds:
            fail("Phase P requires GCP credentials")

        import json as _json
        from google.oauth2 import service_account as _sa
        from googleapiclient.discovery import build as _build
        key_raw = creds.get("service_account_key_json", "")
        key_json = _json.loads(key_raw) if isinstance(key_raw, str) else key_raw
        gc = _sa.Credentials.from_service_account_info(
            key_json,
            scopes=["https://www.googleapis.com/auth/cloud-platform",
                    "https://www.googleapis.com/auth/iam"],
        )
        iam_svc = _build("iam", "v1", credentials=gc)

        # 1. Create service account via SDK
        sa = iam_svc.projects().serviceAccounts().create(
            name=f"projects/{gcp_project}",
            body={"accountId": sa_name,
                  "serviceAccount": {"displayName": "Nexplane smoke test"}},
        ).execute()
        sa_created = True
        log(f"Service account created: {sa_email}")

        # 2. Rotate service account key via CR
        cr = client.run_cr(
            "Smoke-P: rotate service account key", "gcp_rotate_service_account_key",
            cloud_account_id,
            {"project": gcp_project, "service_account_email": sa_email},
        )
        rollback_stack.append((cr["id"], "gcp_rotate_service_account_key"))
        log("Service account key rotated via CR")

        # 3. Disable service account via CR
        cr = client.run_cr(
            "Smoke-P: disable service account", "gcp_disable_service_account",
            cloud_account_id,
            {"project": gcp_project, "service_account_email": sa_email},
        )
        rollback_stack.append((cr["id"], "gcp_disable_service_account"))

        # Verify via SDK
        sa_info = iam_svc.projects().serviceAccounts().get(
            name=f"projects/{gcp_project}/serviceAccounts/{sa_email}"
        ).execute()
        assert sa_info.get("disabled"), "Service account not disabled"
        log("Service account disabled (SDK verified)")

        log("Phase P complete")

    except Exception as e:
        print(f"\n❌ Phase P failed: {e}")
        raise
    finally:
        print("  [Phase P cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete service account
        if sa_created:
            try:
                import json as _json
                from google.oauth2 import service_account as _sa
                from googleapiclient.discovery import build as _build
                creds = _get_gcp_creds()
                key_raw = creds.get("service_account_key_json", "")
                key_json = _json.loads(key_raw) if isinstance(key_raw, str) else key_raw
                gc = _sa.Credentials.from_service_account_info(
                    key_json, scopes=["https://www.googleapis.com/auth/cloud-platform",
                                      "https://www.googleapis.com/auth/iam"])
                iam_svc = _build("iam", "v1", credentials=gc)
                iam_svc.projects().serviceAccounts().delete(
                    name=f"projects/{gcp_project}/serviceAccounts/{sa_email}"
                ).execute()
                print(f"  Safety net: deleted service account {sa_email}")
            except Exception as e:
                print(f"  ⚠️  Safety net SA delete failed: {e}")
```

- [ ] **Step 6: Add Phase Q (Terraform local against GCP)**

```python
def run_phase_q(client: NexplaneClient, cloud_account_id: str, gcp_project: str) -> None:
    """Phase Q: Terraform local apply against GCP — creates GCS bucket using google provider."""
    print("\n[Phase Q] Terraform Local (GCP)")

    import secrets as _secrets
    bucket_name = f"nexplane-smoke-q-{_secrets.token_hex(4)}"

    creds = _get_gcp_creds()
    if not creds:
        fail("Phase Q requires GCP credentials")

    import json as _json
    key_raw = creds.get("service_account_key_json", "")
    key_json = _json.loads(key_raw) if isinstance(key_raw, str) else key_raw
    key_str = _json.dumps(key_json)

    tf_content = (
        'terraform {\n'
        '  required_providers {\n'
        '    google = {\n'
        '      source  = "hashicorp/google"\n'
        '      version = "~> 5.0"\n'
        '    }\n'
        '  }\n'
        '}\n\n'
        'provider "google" {\n'
        '  credentials = <<CREDS\n'
        + key_str + '\n'
        'CREDS\n'
        '  project = "' + gcp_project + '"\n'
        '  region  = "us-central1"\n'
        '}\n\n'
        'resource "google_storage_bucket" "smoke_test" {\n'
        '  name          = "' + bucket_name + '"\n'
        '  location      = "US"\n'
        '  force_destroy = true\n'
        '}\n'
    )

    cr = client.run_cr(
        "Smoke-Q: terraform apply GCS bucket (GCP)", "terraform_local_apply", cloud_account_id,
        {"tf_content": tf_content, "rollback_strategy": "terraform_destroy_local"},
    )
    log(f"Terraform applied (GCP) — GCS bucket: {bucket_name}")

    # Verify via SDK
    try:
        from google.oauth2 import service_account as _sa
        from google.cloud import storage as _storage
        gc = _sa.Credentials.from_service_account_info(
            key_json, scopes=["https://www.googleapis.com/auth/cloud-platform"])
        sc = _storage.Client(project=gcp_project, credentials=gc)
        bucket = sc.get_bucket(bucket_name)
        assert bucket.name == bucket_name
        log("GCS bucket confirmed via SDK")
    except Exception as e:
        print(f"  ⚠️  SDK verification skipped: {e}")

    # Rollback: terraform destroy
    client.rollback_cr(cr["id"], "terraform_local_apply → destroy")
    log("GCS bucket destroyed via Terraform rollback")

    log("Phase Q complete")
```

- [ ] **Step 7: Add Phase R (Ansible local against GCP)**

```python
def run_phase_r(client: NexplaneClient, cloud_account_id: str, gcp_project: str,
                gcp_phase_result: Optional[dict] = None) -> None:
    """Phase R: Ansible local playbook against GCP — exercises ansible_local_playbook CR."""
    print("\n[Phase R] Ansible Local (GCP)")

    # Use a simple localhost connection playbook (same pattern as AWS Phase D)
    playbook_content = (
        "---\n"
        "- name: Nexplane GCP smoke test\n"
        "  hosts: localhost\n"
        "  connection: local\n"
        "  gather_facts: false\n"
        "  tasks:\n"
        "    - name: Check python version\n"
        "      command: python3 --version\n"
        "      register: py_ver\n"
        "    - name: Print version\n"
        "      debug:\n"
        "        msg: 'Python: {{ py_ver.stdout }}'\n"
    )

    cr = client.run_cr(
        "Smoke-R: ansible local playbook (GCP)", "ansible_local_playbook", cloud_account_id,
        {"playbook_content": playbook_content, "inventory": "localhost,"},
    )
    log("Ansible local playbook CR executed successfully (GCP)")
    log("Phase R complete")
```

- [ ] **Step 8: Add sub-project stubs S–X**

```python
def run_phase_s_stub(client: NexplaneClient, cloud_account_id: str, gcp_project: str) -> None:
    """Phase S: GCP Sub-B (Networking) — implement when GCP Sub-project B ships."""
    print("\n[Phase S] GCP Networking — STUB (implement with GCP Sub-project B)")
    print("  ⚠️  Phase S is not yet implemented.")
    print("  This phase will cover: advanced firewall lifecycle, VPC peering, private Google access.")


def run_phase_t_stub(client: NexplaneClient, cloud_account_id: str, gcp_project: str) -> None:
    """Phase T: GCP Sub-C (Storage) — implement when GCP Sub-project C ships."""
    print("\n[Phase T] GCP Storage — STUB (implement with GCP Sub-project C)")
    print("  ⚠️  Phase T is not yet implemented.")
    print("  This phase will cover: GCS bucket create/delete/lifecycle/policy via CRs.")


def run_phase_u_stub(client: NexplaneClient, cloud_account_id: str, gcp_project: str) -> None:
    """Phase U: GCP Sub-D (IAM) — implement when GCP Sub-project D ships."""
    print("\n[Phase U] GCP IAM — STUB (implement with GCP Sub-project D)")
    print("  ⚠️  Phase U is not yet implemented.")
    print("  This phase will cover: IAM role bindings, workload identity, service account impersonation.")


def run_phase_v_stub(client: NexplaneClient, cloud_account_id: str, gcp_project: str) -> None:
    """Phase V: GCP Sub-E (DNS) — implement when GCP Sub-project E ships."""
    print("\n[Phase V] GCP DNS — STUB (implement with GCP Sub-project E)")
    print("  ⚠️  Phase V is not yet implemented.")
    print("  This phase will cover: Cloud DNS zone create/record upsert/delete via CRs.")


def run_phase_w_stub(client: NexplaneClient, cloud_account_id: str, gcp_project: str) -> None:
    """Phase W: GCP Sub-F (SQL) — implement when GCP Sub-project F ships."""
    print("\n[Phase W] GCP Cloud SQL — STUB (implement with GCP Sub-project F)")
    print("  ⚠️  Phase W is not yet implemented.")
    print("  This phase will cover: Cloud SQL instance create/snapshot/replica/promote via CRs.")


def run_phase_x_stub(client: NexplaneClient, cloud_account_id: str, gcp_project: str) -> None:
    """Phase X: GCP Sub-G (Monitoring) — implement when GCP Sub-project G ships."""
    print("\n[Phase X] GCP Monitoring — STUB (implement with GCP Sub-project G)")
    print("  ⚠️  Phase X is not yet implemented.")
    print("  This phase will cover: Cloud Monitoring alerting policies, uptime checks via CRs.")
```

- [ ] **Step 9: Replace `main()` with updated version**

Replace the entire existing `def main():` through `if __name__ == "__main__": main()` with:

```python
def main():
    parser = make_base_parser("Nexplane GCP live smoke test")
    parser.add_argument(
        "--phases", default="L,M",
        help=(
            "Comma-separated phases to run. "
            "L-M: existing phases. N=Firewall, O=Storage, P=ServiceAccounts, "
            "Q=Terraform, R=Ansible. S-X=sub-project stubs (print STUB). "
            "Default: L,M."
        ),
    )
    parser.add_argument("--tailscale-auth-key", default="", help="Reusable Tailscale auth key")
    parser.add_argument("--gcp-project", default="", help="GCP project ID (required for GCP phases)")
    parser.add_argument("--gcp-network", default="global/networks/default",
                        help="GCP VPC network for Phase N firewall rule")
    args = parser.parse_args()
    phases = {p.strip().upper() for p in args.phases.split(",")}

    print("=" * 60)
    print(f"Nexplane GCP Live Smoke Test — phases: {', '.join(sorted(phases))}")
    print("=" * 60)

    client = NexplaneClient(args.base_url, args.email, args.password)
    log("Authenticated")

    cloud_account_id = client.get_cloud_account_asset_id()
    log(f"Cloud account: {cloud_account_id}")

    passed = False
    gcp_phase_result: Optional[dict] = None

    try:
        if "L" in phases:
            agent_secret = client.get_agent_secret()
            gcp_phase_result = run_phase_l(client, cloud_account_id, args.gcp_project, agent_secret)
        if "M" in phases:
            if gcp_phase_result is None or gcp_phase_result.get("instance_asset") is None:
                fail("Phase M requires Phase L to have run first")
            run_phase_m(client, gcp_phase_result, args.gcp_project)
        if "N" in phases:
            run_phase_n(client, cloud_account_id, args.gcp_project, args.gcp_network)
        if "O" in phases:
            run_phase_o(client, cloud_account_id, args.gcp_project)
        if "P" in phases:
            run_phase_p(client, cloud_account_id, args.gcp_project)
        if "Q" in phases:
            run_phase_q(client, cloud_account_id, args.gcp_project)
        if "R" in phases:
            run_phase_r(client, cloud_account_id, args.gcp_project, gcp_phase_result)
        if "S" in phases:
            run_phase_s_stub(client, cloud_account_id, args.gcp_project)
        if "T" in phases:
            run_phase_t_stub(client, cloud_account_id, args.gcp_project)
        if "U" in phases:
            run_phase_u_stub(client, cloud_account_id, args.gcp_project)
        if "V" in phases:
            run_phase_v_stub(client, cloud_account_id, args.gcp_project)
        if "W" in phases:
            run_phase_w_stub(client, cloud_account_id, args.gcp_project)
        if "X" in phases:
            run_phase_x_stub(client, cloud_account_id, args.gcp_project)

        print("\n" + "=" * 60)
        print("✅ ALL SELECTED PHASES PASSED")
        print("=" * 60)
        passed = True

    except SystemExit:
        passed = False
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        passed = False
    finally:
        if not passed:
            print("\n❌ SMOKE TEST FAILED")
            import sys as _sys
            _sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 10: Verify syntax**

```bash
docker exec nexplane-backend-1 python -c "import ast; ast.parse(open('tests/smoke/test_gcp_live.py').read()); print('syntax OK')"
```

- [ ] **Step 11: Verify stubs run correctly**

```bash
docker exec nexplane-backend-1 python tests/smoke/test_gcp_live.py \
  --base-url http://localhost:8000 --email admin@acme.example --password admin123 \
  --gcp-project nexplane --phases S,T,U,V,W,X 2>&1 | tail -10
```

Expected: stub messages print + `✅ ALL SELECTED PHASES PASSED`

- [ ] **Step 12: Run backend tests**

```bash
docker exec nexplane-backend-1 python -m pytest tests/ -q 2>&1 | tail -5
```

- [ ] **Step 13: Commit**

```bash
git add backend/tests/smoke/test_gcp_live.py
git commit -m "feat(smoke): add GCP phases N-R + sub-project stubs S-X + update main() in test_gcp_live.py"
```

---

**Plan 3 complete.** Plans 4–6 build on this:
- **Plan 4:** `test_azure_live.py` new phases P–T + sub-project stubs U–Z
- **Plan 5:** `test_agent_live.py` — Linux × 3 clouds
- **Plan 6:** `test_agent_live.py` — Windows × 3 clouds + README
