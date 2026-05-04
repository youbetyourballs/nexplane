# Expanded AWS Live Smoke Test — Phases A–D Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Extend the AWS live smoke test to cover Nexplane agent deployment via Tailscale VPN, agent-based actions (patching, hardening, log forwarders), local Terraform, and local Ansible — proving these features work against real infrastructure, not just in theory.

**Architecture:** Four independent phases (A–D) run sequentially by default. Each phase is skippable via `--phases`. Cleanup always runs regardless of phase selection or failure. Backend container hosts all tooling (Tailscale, Terraform CLI, Ansible).

**Tech Stack:** Python smoke test script, AWS SSM, Tailscale, nexplane-agent Go binary, Terraform CLI, Ansible + amazon.aws collection, CloudWatch agent.

---

## Prerequisites (one-time setup, already done)

- AWS connector with `SystemAdministrator` policy + `iam:PassRole` for `NexplaneEC2TestRole`
- IAM instance profile `NexplaneEC2TestProfile` with `AmazonSSMManagedInstanceCore` + `CloudWatchAgentServerPolicy`
- Tailscale connector with a reusable pre-authorized auth key stored in Nexplane
- Tailscale tag `tag:nexplane` defined in ACL policy

---

## Phase A: Tailscale Join + Nexplane Agent Deploy

**What it tests:** The full path from a bare EC2 instance to a fully registered Nexplane agent — via network connectivity (Tailscale) then agent installation (SSM).

**Steps:**
1. Install Tailscale in the backend Docker container (idempotent — skip if already running)
2. Join backend container to Tailscale using stored auth key → get container's Tailscale IP
3. Launch EC2 instance (key pair + IAM profile)
4. Wait for SSM agent to register (~90s)
5. `tailscale_join` CR → EC2 joins Tailscale using a fresh ephemeral auth key (generated from stored reusable key)
6. `deploy_nexplane_agent` CR → SSM script downloads agent binary from `http://{tailscale_ip}:8000/downloads/nexplane-agent-linux-amd64`, writes systemd unit, starts service
7. Poll `GET /assets?asset_type=endpoint&q={instance_name}` until agent appears OR poll a dedicated agents endpoint — timeout 3 minutes

**New smoke test parameters for Phase A:**
- No new parameters — everything auto-detected from stored connector credentials

**Backend container Tailscale setup (automated in smoke test):**
```bash
# Run inside backend container via docker compose exec
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up --authkey={stored_key} --hostname=nexplane-backend --accept-routes --accept-dns=false
BACKEND_TAILSCALE_IP=$(tailscale ip -4)
```

**sysctls needed in docker-compose.yml:**
```yaml
backend:
  sysctls:
    - net.ipv4.ip_forward=1
```

---

## Phase B: Agent-Based Actions

**What it tests:** That actions dispatched through Nexplane reach the running agent, execute on the EC2 instance, and return structured results.

**Prerequisite:** Phase A completed — agent registered.

**Steps:**
1. `patch_packages` CR with `dry_run: true` → agent returns list of available security patches without applying
2. `audit_os_security_posture` CR → agent returns per-control CIS audit results
3. `harden_ssh` CR with `dry_run: true` → agent returns proposed SSH config changes without applying
4. `telemetry_agent_deploy` CR with `agent_type: "cloudwatch"` → SSM installs CloudWatch agent, configures it to forward `/var/log/messages` and nexplane-agent logs, verifies service is running

**Gaps to fix for Phase B:**
- `agent_client.py` must dispatch real jobs to the registered agent's poll endpoint, not return mock data
- `apply_linux_patches`, `audit_os_security_posture`, `harden_ssh` backend executors must call agent client, not return stubs
- `telemetry_agent_deploy` for `agent_type: "cloudwatch"` must use SSM (not SSH stub) to install and configure the CloudWatch agent via `AWS-ConfigureAWSPackage` SSM document

**CloudWatch agent SSM installation:**
```json
{
  "document_name": "AWS-ConfigureAWSPackage",
  "parameters": {
    "action": ["Install"],
    "name": ["AmazonCloudWatchAgent"]
  }
}
```

---

## Phase C: Local Terraform

**What it tests:** That a Terraform apply CR actually runs `terraform` CLI, produces a plan, applies it, and rolls back via destroy.

**Backend container setup (added to Dockerfile):**
```dockerfile
RUN curl -fsSL https://releases.hashicorp.com/terraform/1.7.5/terraform_1.7.5_linux_amd64.zip -o /tmp/tf.zip \
    && unzip /tmp/tf.zip -d /usr/local/bin && rm /tmp/tf.zip
```

**New executor: `terraform_local`**
- Writes a temporary `.tf` file to `/tmp/nexplane-tf-{run_id}/`
- Runs `terraform init && terraform plan -out=tfplan` → streams stdout to result
- On approval (second step): runs `terraform apply tfplan`
- Rollback: `terraform destroy -auto-approve`
- Uses AWS credentials from the connected AWS connector (passed as env vars to the subprocess)

**New connector type: `terraform_local`**
- Catalog: single credential field `working_directory` (optional, defaults to temp dir)
- Actions: `terraform_plan_local`, `terraform_apply_local`, `terraform_destroy_local`

**Test Terraform module (written by the smoke test):**
```hcl
resource "aws_s3_bucket" "smoke_test" {
  bucket = "nexplane-smoke-test-${random_id.suffix.hex}"
  force_destroy = true
}

resource "random_id" "suffix" {
  byte_length = 4
}
```

**Smoke test steps for Phase C:**
1. Write test `.tf` content to a temp file
2. `terraform_apply` CR targeting the AWS cloud_account asset with the `.tf` content as a parameter
3. Assert bucket appears in `discover_s3_buckets` results
4. `terraform_destroy` CR → bucket deleted
5. Assert bucket absent from discovery

**Real TFC path:** Existing `terraform` connector + `plan_workspace`/`apply_workspace` executors work unchanged once TFC credentials are configured. `terraform_local` is an additive parallel path.

---

## Phase D: Local Ansible

**What it tests:** That an Ansible playbook CR actually runs `ansible-playbook`, executes against an EC2 instance, and returns structured results.

**Backend container setup (added to Dockerfile):**
```dockerfile
RUN pip install ansible boto3 botocore \
    && ansible-galaxy collection install amazon.aws community.aws
```

**New executor: `ansible_local`**
- Writes inventory file to `/tmp/nexplane-ansible-{run_id}/inventory.ini`
- Writes playbook `.yml` to the same temp dir
- Runs `ansible-playbook -i inventory.ini playbook.yml` with SSM as transport:
  ```ini
  [targets]
  {instance_id} ansible_connection=aws_ssm ansible_aws_ssm_region={region}
  ```
- Returns stdout/stderr + task results
- Uses AWS credentials from connected AWS connector (env vars)

**New connector type: `ansible_local`**
- No credential fields needed (uses AWS connector credentials passed at execution time)
- Actions: `ansible_run_local`, `ansible_check_local`

**Test playbook:**
```yaml
---
- name: Smoke test playbook
  hosts: all
  gather_facts: yes
  tasks:
    - name: Install htop
      ansible.builtin.package:
        name: htop
        state: present
      become: yes

    - name: Verify htop installed
      ansible.builtin.command: htop --version
      register: htop_version
      changed_when: false

    - name: Report
      ansible.builtin.debug:
        msg: "htop version: {{ htop_version.stdout }}"
```

**Smoke test steps for Phase D:**
1. `ansible_check` CR (--check mode) → verify no errors in dry run
2. `ansible_run` CR → htop installed on EC2 instance
3. `ssm_command` CR → `htop --version` to confirm installation
4. Cleanup: `ansible_run` CR to remove htop (state: absent)

**Real AWX path:** Existing `ansible` connector + `launch_job` executor works unchanged once AWX credentials are configured. `ansible_local` is additive.

---

## Smoke Test CLI Interface

```bash
python backend/tests/smoke/test_aws_live.py \
  --base-url http://localhost:8000 \
  --email admin@nexplane.local \
  --password changeme \
  --phases A,B,C,D          # default: all phases
```

**Phase flag examples:**
```bash
--phases A           # EC2 + Tailscale + agent only
--phases A,B         # + agent actions
--phases C           # Terraform only (no EC2 needed)
--phases A,B,C,D     # everything (default)
```

Phases C and D can run standalone if an EC2 instance is already running (detected by name in inventory). Phases B requires A to have completed in the same run or a previous run with the agent still registered.

---

## Full Smoke Test Sequence (all phases)

```
Setup:     Install Tailscale in backend container, get Tailscale IP
           Read agent_secret from GET /settings
           Read AWS connector credentials

Phase A:
  1.  Create key pair CR
  2.  Launch EC2 CR (iam_instance_profile=NexplaneEC2TestProfile, key_name=nexplane-smoke-test-key)
  3.  Wait 90s for SSM agent
  4.  tailscale_join CR → EC2 on Tailscale
  5.  deploy_nexplane_agent CR → agent installed, phones home to backend Tailscale IP
  6.  Poll until agent appears in inventory (3 min timeout)

Phase B:
  7.  patch_packages CR (dry_run=true) → patch list returned
  8.  audit_os_security_posture CR → CIS results returned
  9.  harden_ssh CR (dry_run=true) → proposed changes returned
  10. telemetry_agent_deploy (cloudwatch) CR → CloudWatch agent installed

Phase C:
  11. terraform_apply CR (local) → S3 bucket created
  12. discover_s3_buckets → assert bucket present
  13. terraform_destroy CR → bucket deleted
  14. discover_s3_buckets → assert bucket absent

Phase D:
  15. ansible_check CR → dry run passes
  16. ansible_run CR → htop installed
  17. ssm_command CR → htop --version confirms install
  18. ansible_run CR (remove) → htop removed

Cleanup:   tailscale_remove CR
           ec2_terminate CR
           delete key pair CR
           delete EBS snapshots (direct boto3)
           tailscale down in backend container
```

---

## Dockerfile Changes Required

```dockerfile
# backend/Dockerfile — add to the build stage:

# Tailscale (for agent connectivity)
RUN curl -fsSL https://pkgs.tailscale.com/stable/debian/bookworm.nokey | apt-key add - \
    && echo "deb https://pkgs.tailscale.com/stable/debian bookworm main" > /etc/apt/sources.list.d/tailscale.list \
    && apt-get update && apt-get install -y tailscale \
    && rm -rf /var/lib/apt/lists/*

# Terraform CLI (for local IaC)
RUN curl -fsSL https://releases.hashicorp.com/terraform/1.7.5/terraform_1.7.5_linux_amd64.zip -o /tmp/tf.zip \
    && unzip /tmp/tf.zip -d /usr/local/bin && rm /tmp/tf.zip \
    && chmod +x /usr/local/bin/terraform

# Ansible + AWS collections (for local Ansible)
RUN pip install --no-cache-dir ansible boto3 botocore \
    && ansible-galaxy collection install amazon.aws community.aws
```

```yaml
# docker-compose.yml — backend service:
backend:
  sysctls:
    - net.ipv4.ip_forward=1
  cap_add:
    - NET_ADMIN
```

---

## Gaps to Fix in Existing Code (Phase B)

1. **`agent_client.py`** — verify it dispatches real jobs via HTTP to the agent's registered poll URL, not mock
2. **`apply_linux_patches.py`** — must call agent_client, not return stub
3. **`audit_os_security_posture.py`** — must call agent_client, not return stub  
4. **`harden_ssh.py`** — must call agent_client, not return stub
5. **`telemetry_agent_deploy`** change type — add CloudWatch agent step using `run_ssm_command` with `AWS-ConfigureAWSPackage` document

## New Code for Phases C and D

1. **`terraform_local` connector** — catalog JSON, executor files, change type definitions
2. **`ansible_local` connector** — catalog JSON, executor files using SSM transport, change type definitions
3. **Dockerfile** — add Tailscale, Terraform, Ansible
4. **docker-compose.yml** — add sysctls and NET_ADMIN cap
5. **`test_aws_live.py`** — extend with `--phases` flag and all new steps
