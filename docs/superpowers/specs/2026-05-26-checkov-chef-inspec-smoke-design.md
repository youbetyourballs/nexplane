# Checkov + Chef InSpec Smoke Coverage Design

> **Scope:** Group B of Tier 2 smoke backlog. Two new smoke phases: `checkov` (scan_iac, scan_secrets, get_compliance_summary) and `chef_inspec` (discover_nodes, run_compliance_scan, discover_compliance_results). Groups A (cloudformation, bicep, helm) and C (azure_ad, defender_endpoint) are separate sessions.

**Goal:** Add live smoke coverage for checkov and chef_inspec connectors by running real executors against real infrastructure through the Nexplane CR pipeline.

**Method:** CR pipeline throughout (dogfooding principle). Checkov runs entirely in the backend container (no external infra). Chef InSpec uses an AMI-cached EC2 instance running Chef Automate with a self-scan topology.

---

## Phase CHECKOV (`test_aws_live.py`)

**Infra:** None. Checkov 3.2.529 is already installed in the backend container at `/usr/local/bin/checkov`. The executor scans a local filesystem path — no network calls, no external service.

**Setup (in-process, no EC2 needed):**
- Create `/tmp/nexplane-smoke-checkov-<suffix>/` in the backend container
- Write a minimal `main.tf` with a known misconfiguration:

```hcl
resource "aws_s3_bucket" "smoke" {
  bucket = "nexplane-smoke-bucket"
}
```

This file intentionally omits versioning and ACL configuration, triggering at least `CKV_AWS_21` (S3 versioning) and `CKV2_AWS_61` (S3 lifecycle policy) under the terraform framework.

**Connector registration:**
- POST `/connectors` with `connector_type: "checkov"`, `name: "nexplane-smoke-checkov-<suffix>"`
- PUT `/connectors/{id}/credentials` with `{"repo_path": "/tmp/nexplane-smoke-checkov-<suffix>", "framework": "terraform"}`
- Register one asset of type `code_repository`

**CR sequence (all with `_locked_connector_type: "checkov"`):**

1. `scan_iac` — assert `failed > 0` and `findings` list is non-empty
2. `get_compliance_summary` — assert result contains `passed` and `failed` numeric keys
3. `scan_secrets` — assert result contains `count` key (0 is acceptable — temp dir has no secrets)

**Cleanup:** `rm -rf /tmp/nexplane-smoke-checkov-<suffix>` via subprocess after CRs complete.

---

## Phase CHEF_INSPEC (`test_aws_live.py`)

**Infra:** AMI-cached EC2 instance running Chef Automate. Self-scan topology: Chef Automate and the scan target are the same instance (Chef Automate SSHes to `127.0.0.1`).

### AMI Bootstrap (run once, cached)

AMI cache key: `hashlib.md5(b"chef-automate-2-self-scan-inspec-v1").hexdigest()`
SSM param: `/nexplane/smoke-amis/chef-automate/{hash}`

Bootstrap script (SSM RunCommand on a fresh `t3.xlarge` Amazon Linux 2023 instance):

```bash
# 1. Install Chef Automate
curl -fsSL https://packages.chef.io/files/current/latest/chef-automate-cli/chef-automate_linux_amd64.zip | gunzip - > /usr/local/bin/chef-automate
chmod +x /usr/local/bin/chef-automate
mkdir -p /hab/a2
cd /hab/a2
chef-automate init-config --fqdn 127.0.0.1
# Patch config: disable external_fqdn TLS check, set license to trial
chef-automate deploy config.toml --accept-terms-and-mlsa --skip-preflight

# 2. Create API token
API_TOKEN=$(chef-automate iam token create nexplane-smoke --admin | tail -1)
aws ssm put-parameter --name /nexplane/smoke/chef-automate/api-token --value "$API_TOKEN" --type SecureString --overwrite

# 3. SSH keypair for self-scan
ssh-keygen -t ed25519 -f /root/.ssh/chef_smoke_key -N ""
cat /root/.ssh/chef_smoke_key.pub >> /root/.ssh/authorized_keys
PRIVKEY=$(cat /root/.ssh/chef_smoke_key)
aws ssm put-parameter --name /nexplane/smoke/chef-automate/ssh-privkey --value "$PRIVKEY" --type SecureString --overwrite

# 4. Upload minimal InSpec profile
mkdir -p /tmp/smoke-profile/controls
cat > /tmp/smoke-profile/inspec.yml <<PROF
name: nexplane-smoke
title: Nexplane Smoke Profile
version: 0.1.0
PROF
cat > /tmp/smoke-profile/controls/hosts.rb <<CTRL
control 'smoke-01' do
  title 'hosts file exists'
  describe file('/etc/hosts') do
    it { should exist }
  end
end
CTRL
cd /tmp && zip -r smoke-profile.zip smoke-profile/
# Upload via Chef Automate API
curl -k -H "api-token: $API_TOKEN" \
  -F "file=@smoke-profile.zip" \
  https://127.0.0.1/api/v0/compliance/profiles?owner=admin

# 5. Register self as a node
NODE_RESP=$(curl -sk -H "api-token: $API_TOKEN" -H "Content-Type: application/json" \
  -d "{\"name\":\"smoke-self\",\"manager\":\"automate\",\"target_config\":{\"backend\":\"ssh\",\"host\":\"127.0.0.1\",\"port\":22,\"user\":\"root\",\"key_files\":[\"/root/.ssh/chef_smoke_key\"]}}" \
  https://127.0.0.1/api/v0/nodes)
NODE_ID=$(echo "$NODE_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
aws ssm put-parameter --name /nexplane/smoke/chef-automate/node-id --value "$NODE_ID" --type String --overwrite

echo "CHEF_AUTOMATE_SETUP_COMPLETE"
```

Instance type: `t3.xlarge` (4 vCPU, 16GB — Chef Automate minimum requirement).

### Smoke Sequence

**EC2 launch:** From cached AMI. On boot, restart Chef Automate services:
```bash
systemctl start chef-automate
# Wait until API is ready (poll /api/v0/version, max 3 min)
```

**Retrieve from SSM:** `api_token`, `node_id`. Profile name is hardcoded: `"nexplane-smoke"`, owner `"admin"`.

**Connector registration:**
- POST `/connectors` with `connector_type: "chef_inspec"`, `name: "nexplane-smoke-chef-<suffix>"`
- PUT `/connectors/{id}/credentials` with `{"automate_url": "https://<private-ip>", "api_token": "<token>"}`
- Register one asset of type `compliance_server`

**CR sequence (all with `_locked_connector_type: "chef_inspec"`):**

1. `discover_nodes` — assert pre-registered `smoke-self` node appears in results
2. `run_compliance_scan` — `node_id=<from SSM>`, `profile_id="admin/nexplane-smoke"` — assert `job_id` returned and `scan_triggered: True`
3. **Poll** (direct API, not CR): GET `/api/v0/compliance/scanner/jobs/<job_id>` every 30s, max 8 min, until `status == "completed"`
4. `discover_compliance_results` — assert node appears with status `"passed"` or `"failed"` (not `"unknown"` — `unknown` means no scan results stored)

**Cleanup:** Terminate EC2 instance. Chef Automate state is ephemeral (AMI is read-only base).

---

## Files Modified

| Action | Path |
|--------|------|
| Modify | `backend/tests/smoke/test_aws_live.py` — add `run_phase_checkov()` and `run_phase_chef_inspec()`, wire into `main()` |

No executor changes. No migrations. No new connector types (both already registered in the connector catalog).

---

## Non-Goals

- Group C (azure_ad, defender_endpoint) — separate session
- `assign_profile` executor smoke — requires Chef Automate node manager setup; deferred
- `discover_compliance_profiles` executor smoke — profiles list from Chef Automate marketplace requires internet; deferred
- `scan_secrets` finding assertions — asserting count > 0 would require committing fake secrets to the temp dir; not worth it
