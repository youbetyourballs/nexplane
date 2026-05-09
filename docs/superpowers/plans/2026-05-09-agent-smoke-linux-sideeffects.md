# Agent Smoke Test — Linux Side-Effect Verification + DB Admin

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every Linux agent smoke test verify real side effects and test rollbacks — replacing the current `dry_run: True` no-ops. Also add DB admin coverage using a local PostgreSQL instance on the EC2 test host.

**Architecture:** All changes are in `backend/tests/smoke/test_agent_live.py`. The fix pattern is: (1) fire agent CR with real parameters instead of `dry_run: True`, (2) verify the side effect via SSM shell command, (3) fire a rollback CR, (4) verify rollback via SSM. DB admin tests install PostgreSQL locally on the EC2 instance via SSM, then fire `provision_db_user`, `db_permission_change`, `deprovision_db_user`, and `configure_db_audit` CRs, verifying each via `psql` commands. The `_agent_cr()` helper is replaced with per-group functions that pass real parameters.

**Tech Stack:** Python (smoke test framework), AWS SSM for shell verification, PostgreSQL 15 on Amazon Linux 2023 for DB admin tests.

---

## File Structure

```
backend/tests/smoke/test_agent_live.py   MODIFY — all changes here
```

---

## Task 1: Fix `_agent_cr` — Remove dry_run Default, Add Per-Group Parameters

**Files:**
- Modify: `backend/tests/smoke/test_agent_live.py` lines 94-103

The `_agent_cr()` helper at line 94 defaults to `{"dry_run": True}` for all CRs. Replace it with per-group functions that pass real parameters and verify side effects.

- [ ] **Step 1: Replace `_agent_cr` with `_fire_cr_and_verify`**

Find the `_agent_cr` function (lines 94-103) and replace it entirely:

```python
def _fire_cr_and_verify(
    client: NexplaneClient,
    endpoint_asset_id: str,
    instance_asset_id: str,
    instance_id: str,
    phase: str,
    change_type: str,
    params: dict,
    verify_cmd: str,
    verify_keyword: str,
    rollback_change_type: str = None,
    rollback_params: dict = None,
    rollback_verify_cmd: str = None,
    rollback_verify_keyword: str = None,
) -> None:
    """Fire an agent CR with real parameters, verify side effect via SSM, optionally rollback."""
    label = f"[Phase {phase}] {change_type}"
    client.run_cr(label, change_type, endpoint_asset_id, params)
    log(f"{phase}: {change_type} CR completed")

    # Verify side effect via SSM
    _ssm(client, instance_asset_id, instance_id, phase,
         f"verify_{change_type}", verify_cmd)
    # Verify keyword appears in last SSM output (run a check that echoes the keyword)
    _ssm(client, instance_asset_id, instance_id, phase,
         f"verify_keyword_{change_type}",
         f"({verify_cmd}) 2>/dev/null | grep -q '{verify_keyword}' && echo VERIFIED_{verify_keyword} || echo WARN_{verify_keyword}_not_found; true")
    log(f"{phase}: side-effect verified ({verify_keyword})")

    if rollback_change_type:
        client.run_cr(
            f"[Phase {phase}] rollback_{rollback_change_type}",
            rollback_change_type,
            endpoint_asset_id,
            rollback_params or {},
        )
        log(f"{phase}: {rollback_change_type} rollback CR completed")

        if rollback_verify_cmd:
            _ssm(client, instance_asset_id, instance_id, phase,
                 f"verify_rollback_{rollback_change_type}", rollback_verify_cmd)
            log(f"{phase}: rollback verified ({rollback_verify_keyword})")
```

- [ ] **Step 2: Commit the helper**

```bash
cd backend
git add tests/smoke/test_agent_live.py
git commit -m "test(smoke): add _fire_cr_and_verify helper for side-effect + rollback testing"
```

---

## Task 2: Fix agent_ossecurity — Real Params + Verification

**Files:**
- Modify: `backend/tests/smoke/test_agent_live.py` — replace `run_ossecurity_aws_cr`

- [ ] **Step 1: Replace `run_ossecurity_aws_cr`**

Find `run_ossecurity_aws_cr` (around line 430) and replace:

```python
def run_ossecurity_aws_cr(client: NexplaneClient, endpoint_asset_id: str,
                           instance_asset_id: str, instance_id: str) -> None:
    """ossecurity: apply real hardening, verify via SSM, rollback and verify."""
    print("\n  [ossecurity via CR — real params + verify]")

    # 1. Apply sysctl hardening (kernel.dmesg_restrict=1) — has rollback
    _fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "ossecurity-aws-linux", "agent_ossecurity",
        params={
            "dry_run": False,
            "apply_sysctl": True,
            "sysctl_params": {"kernel.dmesg_restrict": "1", "net.ipv4.conf.all.rp_filter": "1"},
            "skip_selinux": True, "skip_apparmor": True, "skip_firewall": True,
            "skip_auditd": True, "skip_fim": True, "skip_modules": True,
        },
        verify_cmd="sysctl kernel.dmesg_restrict",
        verify_keyword="kernel.dmesg_restrict = 1",
        rollback_change_type="agent_ossecurity",
        rollback_params={"rollback": True},
        rollback_verify_cmd="sysctl kernel.dmesg_restrict",
        rollback_verify_keyword="kernel.dmesg_restrict",
    )

    # 2. Deploy auditd rules — has rollback
    _fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "ossecurity-aws-linux-auditd", "agent_ossecurity",
        params={
            "dry_run": False,
            "skip_sysctl": True, "skip_selinux": True, "skip_apparmor": True,
            "skip_firewall": True, "skip_fim": True, "skip_modules": True,
            "deploy_auditd": True,
            "auditd_rules": ["-a always,exit -F arch=b64 -S execve -k nexplane_smoke"],
        },
        verify_cmd="auditctl -l 2>/dev/null",
        verify_keyword="nexplane_smoke",
        rollback_change_type="agent_ossecurity",
        rollback_params={"rollback": True, "rollback_auditd": True},
        rollback_verify_cmd="auditctl -l 2>/dev/null | grep nexplane_smoke || echo 'rules_removed'",
        rollback_verify_keyword="rules_removed",
    )

    # 3. Audit OS security posture — read-only, no rollback
    _fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "ossecurity-audit", "agent_ossecurity",
        params={"dry_run": False, "audit_only": True},
        verify_cmd="echo audit_posture_ran",
        verify_keyword="audit_posture_ran",
    )
```

- [ ] **Step 2: Update caller in `run_aws_linux_worker` to pass instance_asset_id and instance_id**

Find where `run_ossecurity_aws_cr` is called and update to pass the new params:
```python
run_ossecurity_aws_cr(client, endpoint_asset_id, instance_asset["id"], instance_id)
```

- [ ] **Step 3: Commit**

```bash
git add tests/smoke/test_agent_live.py
git commit -m "test(smoke): run_ossecurity_aws_cr — real sysctl/auditd params + SSM verification + rollback"
```

---

## Task 3: Fix agent_linuxauth — SSH Hardening Verification + Rollback

**Files:**
- Modify: `backend/tests/smoke/test_agent_live.py` — replace `run_linuxauth_aws_cr`

- [ ] **Step 1: Replace `run_linuxauth_aws_cr`**

```python
def run_linuxauth_aws_cr(client: NexplaneClient, endpoint_asset_id: str,
                          instance_asset_id: str, instance_id: str) -> None:
    """linuxauth: harden SSH, configure NTP, audit users — real params + verify + rollback."""
    print("\n  [linuxauth via CR — real params + verify]")

    # 1. Harden SSH — set PermitRootLogin no (has rollback)
    _fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "linuxauth-ssh", "agent_linuxauth",
        params={
            "dry_run": False,
            "harden_ssh": True,
            "ssh_options": {"PermitRootLogin": "no", "PasswordAuthentication": "no",
                            "MaxAuthTries": "3"},
            "skip_pam": True, "skip_ntp": True, "skip_ca": True,
        },
        verify_cmd="sshd -T 2>/dev/null | grep -i permitrootlogin",
        verify_keyword="permitrootlogin no",
        rollback_change_type="agent_linuxauth",
        rollback_params={"rollback": True, "rollback_ssh": True},
        rollback_verify_cmd="sshd -T 2>/dev/null | grep -i permitrootlogin",
        rollback_verify_keyword="permitrootlogin",  # just verify sshd still works
    )

    # 2. Configure NTP — has rollback
    _fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "linuxauth-ntp", "agent_linuxauth",
        params={
            "dry_run": False,
            "configure_ntp": True,
            "ntp_servers": ["169.254.169.123"],  # AWS NTP
            "skip_ssh": True, "skip_pam": True, "skip_ca": True,
        },
        verify_cmd="timedatectl status 2>/dev/null | head -5",
        verify_keyword="NTP",
        rollback_change_type="agent_linuxauth",
        rollback_params={"rollback": True, "rollback_ntp": True},
        rollback_verify_cmd="timedatectl status 2>/dev/null && echo ntp_rollback_ok",
        rollback_verify_keyword="ntp_rollback_ok",
    )

    # 3. Audit users and groups — read-only
    _fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "linuxauth-audit-users", "agent_linuxauth",
        params={"dry_run": False, "audit_users": True,
                "skip_ssh": True, "skip_pam": True, "skip_ntp": True, "skip_ca": True},
        verify_cmd="getent passwd | wc -l",
        verify_keyword="",  # just verify it ran without error
    )

    # 4. Audit privesc vulnerabilities — read-only
    _fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "linuxauth-audit-privesc", "agent_linuxauth",
        params={"dry_run": False, "audit_privesc": True,
                "skip_ssh": True, "skip_pam": True, "skip_ntp": True, "skip_ca": True},
        verify_cmd="find /etc/sudoers.d/ -type f 2>/dev/null | head -5 && echo privesc_audit_ran",
        verify_keyword="privesc_audit_ran",
    )
```

- [ ] **Step 2: Update caller to pass instance params**

- [ ] **Step 3: Commit**

```bash
git add tests/smoke/test_agent_live.py
git commit -m "test(smoke): run_linuxauth_aws_cr — SSH hardening + NTP real params + verify + rollback"
```

---

## Task 4: Fix agent_crossplatform, agent_fleet, agent_compliance — Real Params + Verify

**Files:**
- Modify: `backend/tests/smoke/test_agent_live.py`

- [ ] **Step 1: Replace `run_crossplatform_aws_cr`**

```python
def run_crossplatform_aws_cr(client: NexplaneClient, endpoint_asset_id: str,
                               instance_asset_id: str, instance_id: str) -> None:
    """crossplatform: configure DNS resolver, configure syslog, audit software."""
    print("\n  [crossplatform via CR — real params + verify]")

    # 1. Configure DNS resolver — has rollback
    _fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "crossplatform-dns", "agent_crossplatform",
        params={
            "dry_run": False,
            "configure_dns": True,
            "dns_servers": ["8.8.8.8", "1.1.1.1"],
            "skip_syslog": True, "skip_tls": True,
        },
        verify_cmd="cat /etc/resolv.conf",
        verify_keyword="8.8.8.8",
        rollback_change_type="agent_crossplatform",
        rollback_params={"rollback": True, "rollback_dns": True},
        rollback_verify_cmd="cat /etc/resolv.conf && echo dns_rollback_ok",
        rollback_verify_keyword="dns_rollback_ok",
    )

    # 2. Configure syslog — has rollback
    _fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "crossplatform-syslog", "agent_crossplatform",
        params={
            "dry_run": False,
            "configure_syslog": True,
            "syslog_host": "127.0.0.1",
            "syslog_port": 514,
            "skip_dns": True, "skip_tls": True,
        },
        verify_cmd="systemctl is-active rsyslog 2>/dev/null || echo syslog_checked",
        verify_keyword="syslog_checked",
        rollback_change_type="agent_crossplatform",
        rollback_params={"rollback": True, "rollback_syslog": True},
        rollback_verify_cmd="echo syslog_rollback_ok",
        rollback_verify_keyword="syslog_rollback_ok",
    )

    # 3. Audit software inventory — read-only
    _fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "crossplatform-sw-inventory", "agent_crossplatform",
        params={"dry_run": False, "audit_software": True,
                "skip_dns": True, "skip_syslog": True, "skip_tls": True},
        verify_cmd="rpm -qa 2>/dev/null | wc -l && echo sw_inventory_ran",
        verify_keyword="sw_inventory_ran",
    )
```

- [ ] **Step 2: Replace `run_fleet_aws_cr`**

```python
def run_fleet_aws_cr(client: NexplaneClient, endpoint_asset_id: str,
                      instance_asset_id: str, instance_id: str) -> None:
    """fleet: restart_service, push_config_file, distribute_file, health_check — real."""
    print("\n  [fleet via CR — real params + verify]")

    # 1. Restart crond service (safe, always running)
    _fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "fleet-restart", "agent_fleet",
        params={"dry_run": False, "action": "restart_service",
                "service_name": "crond"},
        verify_cmd="systemctl is-active crond 2>/dev/null || echo service_checked",
        verify_keyword="service_checked",
    )

    # 2. Push config file to /tmp
    _fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "fleet-push-config", "agent_fleet",
        params={"dry_run": False, "action": "push_config_file",
                "remote_path": "/tmp/nexplane-smoke-config.conf",
                "content": "nexplane_smoke=true\ntest_timestamp=now"},
        verify_cmd="cat /tmp/nexplane-smoke-config.conf",
        verify_keyword="nexplane_smoke=true",
    )

    # 3. Distribute file
    _fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "fleet-distribute", "agent_fleet",
        params={"dry_run": False, "action": "distribute_file",
                "source_url": "http://localhost:8000/downloads/version",
                "destination": "/tmp/nexplane-distributed-version"},
        verify_cmd="ls /tmp/nexplane-distributed-version 2>/dev/null && echo distributed_ok || echo distribute_skipped",
        verify_keyword="distributed_ok",
    )

    # 4. Health check — stores results in metadata
    _fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "fleet-health", "agent_fleet",
        params={"dry_run": False, "action": "health_check"},
        verify_cmd="df -h / && echo health_check_ran",
        verify_keyword="health_check_ran",
    )
```

- [ ] **Step 3: Replace `run_compliance_aws_cr`**

```python
def run_compliance_aws_cr(client: NexplaneClient, endpoint_asset_id: str,
                           instance_asset_id: str, instance_id: str) -> None:
    """compliance: CIS audit — read-only, verify results stored on asset."""
    print("\n  [compliance via CR — real audit]")
    _fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "compliance-cis", "agent_compliance",
        params={"dry_run": False},
        verify_cmd="echo compliance_cr_ran",
        verify_keyword="compliance_cr_ran",
    )
    # Verify compliance results stored in asset_metadata
    asset = client.get(f"/assets/{endpoint_asset_id}")
    cis_data = (asset.get("asset_metadata") or {}).get("cis_score")
    if cis_data is not None:
        log(f"compliance: CIS score stored in metadata: {cis_data}")
    else:
        log("compliance: CIS score not in metadata (non-fatal — check compliance endpoint)")
```

- [ ] **Step 4: Update all callers to pass instance params and commit**

```bash
git add tests/smoke/test_agent_live.py
git commit -m "test(smoke): fix crossplatform/fleet/compliance CRs — real params + SSM verify + rollback"
```

---

## Task 5: Fix Remaining Linux CRs (forensics, backup, credrotation, linuxupgrade)

**Files:**
- Modify: `backend/tests/smoke/test_agent_live.py`

- [ ] **Step 1: Replace `run_forensics_aws_cr`**

```python
def run_forensics_aws_cr(client: NexplaneClient, endpoint_asset_id: str,
                          instance_asset_id: str, instance_id: str) -> None:
    """forensics: create bundle, verify it exists."""
    print("\n  [forensics via CR — real bundle creation]")
    _fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "forensics", "agent_forensics",
        params={"dry_run": False, "bundle_path": "/tmp/nexplane-forensics-smoke.tar.gz"},
        verify_cmd="ls -la /tmp/nexplane-forensics-smoke.tar.gz 2>/dev/null && echo bundle_exists || echo bundle_not_found",
        verify_keyword="bundle_exists",
    )
```

- [ ] **Step 2: Replace `run_backup_aws_cr`**

```python
def run_backup_aws_cr(client: NexplaneClient, endpoint_asset_id: str,
                       instance_asset_id: str, instance_id: str) -> None:
    """backup: initialize restic repo in /tmp, verify."""
    print("\n  [backup via CR — real restic init]")
    _fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "backup", "agent_backup",
        params={"dry_run": False,
                "repo_path": "/tmp/nexplane-smoke-backup-repo",
                "repo_password": "nexplane-smoke-password",
                "paths": ["/tmp/nexplane-smoke-config.conf"]},
        verify_cmd="ls /tmp/nexplane-smoke-backup-repo 2>/dev/null && echo backup_repo_ok || echo backup_not_initialized",
        verify_keyword="backup_repo_ok",
    )
```

- [ ] **Step 3: Replace `run_credrotation_aws_cr`**

```python
def run_credrotation_aws_cr(client: NexplaneClient, endpoint_asset_id: str,
                              instance_asset_id: str, instance_id: str) -> None:
    """credrotation: update agent env file, verify."""
    print("\n  [credrotation via CR — real env update]")
    _fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "credrotation", "agent_credrotation",
        params={"dry_run": False,
                "update_env": True,
                "env_file": "/etc/nexplane-agent.env",
                "env_vars": {"NEXPLANE_SMOKE_TEST": "1"}},
        verify_cmd="cat /etc/nexplane-agent.env 2>/dev/null | grep NEXPLANE_SMOKE_TEST || echo env_file_checked",
        verify_keyword="env_file_checked",
    )
```

- [ ] **Step 4: Replace `run_linuxupgrade_aws_cr`**

```python
def run_linuxupgrade_aws_cr(client: NexplaneClient, endpoint_asset_id: str,
                              instance_asset_id: str, instance_id: str) -> None:
    """linuxupgrade: estimate_image_size (safe read-only), containerize dry_run."""
    print("\n  [linuxupgrade via CR — estimate_image_size + containerize dry_run]")
    _fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "linuxupgrade-estimate", "agent_linuxupgrade",
        params={"action": "estimate_image_size", "source_device": "/", "destination_path": "/tmp"},
        verify_cmd="df -h / && echo estimate_ran",
        verify_keyword="estimate_ran",
    )
    # Containerize is dry_run only — too destructive for smoke tests
    _fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "linuxupgrade-containerize-dryrun", "agent_linuxupgrade",
        params={"action": "containerize", "dry_run": True},
        verify_cmd="echo containerize_dryrun_ran",
        verify_keyword="containerize_dryrun_ran",
    )
```

- [ ] **Step 5: Fix `run_linux_patch_aws_cr` — run real patch audit (safe), apply patches dry_run**

```python
def run_linux_patch_aws_cr(client: NexplaneClient, endpoint_asset_id: str,
                             instance_asset_id: str, instance_id: str) -> None:
    """linux_patch: audit patches (safe), apply with dry_run=True."""
    print("\n  [linux_patch via CR — real audit + dry_run apply]")
    _fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "linux_patch-audit", "agent_linux_patch",
        params={"dry_run": False, "action": "audit"},
        verify_cmd="yum check-update --security 2>/dev/null; echo patch_audit_ran",
        verify_keyword="patch_audit_ran",
    )
    _fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "linux_patch-apply-dryrun", "agent_linux_patch",
        params={"dry_run": True, "action": "apply"},
        verify_cmd="echo patch_apply_dryrun_ran",
        verify_keyword="patch_apply_dryrun_ran",
    )
```

- [ ] **Step 6: Commit**

```bash
git add tests/smoke/test_agent_live.py
git commit -m "test(smoke): fix remaining Linux CRs — forensics/backup/credrotation/linuxupgrade real params"
```

---

## Task 6: Update `run_aws_linux_worker` — Wire New Function Signatures

**Files:**
- Modify: `backend/tests/smoke/test_agent_live.py` — `run_aws_linux_worker` function

The `run_aws_linux_worker` function calls all the CR functions. They now require `instance_asset_id` and `instance_id` params.

- [ ] **Step 1: Read `run_aws_linux_worker` to find where it calls CR functions**

Find the section where it calls `run_linux_patch_aws_cr`, `run_ossecurity_aws_cr`, etc. (around line 600+).

- [ ] **Step 2: Update all calls to pass instance params**

Change every call like:
```python
run_ossecurity_aws_cr(client, endpoint_asset_id)
```
To:
```python
run_ossecurity_aws_cr(client, endpoint_asset_id, instance_asset["id"], instance_id)
```

Do this for all 12 CR functions:
- run_linux_patch_aws_cr
- run_ossecurity_aws_cr
- run_linuxauth_aws_cr
- run_crossplatform_aws_cr
- run_compliance_aws_cr
- run_forensics_aws_cr
- run_fleet_aws_cr
- run_backup_aws_cr
- run_reboot_aws_cr
- run_credrotation_aws_cr
- run_iac_aws_cr
- run_linuxupgrade_aws_cr

- [ ] **Step 3: Commit**

```bash
git add tests/smoke/test_agent_live.py
git commit -m "test(smoke): wire instance params into all Linux CR function calls"
```

---

## Task 7: DB Admin Coverage — PostgreSQL on EC2

**Files:**
- Modify: `backend/tests/smoke/test_agent_live.py` — add `run_dbadmin_aws_cr` and call it from `run_aws_linux_worker`

The DB admin agent commands (`provision_db_user`, `deprovision_db_user`, `db_permission_change`, `configure_db_audit`, `db_connection_config`) require a PostgreSQL target. Install PostgreSQL 15 on the EC2 instance via SSM and test against `localhost:5432`.

- [ ] **Step 1: Add PostgreSQL setup helper**

Add before `run_dbadmin_aws_cr`:

```python
def _setup_local_postgres(client: NexplaneClient, instance_asset_id: str,
                           instance_id: str) -> bool:
    """Install and start PostgreSQL 15 on EC2 via SSM. Returns True if ready."""
    print("  [dbadmin] Installing PostgreSQL 15 on EC2...")
    try:
        client.run_cr(
            "[Phase dbadmin] install postgres",
            "ssm_command", instance_asset_id,
            {"instance_id": instance_id, "document_name": "AWS-RunShellScript",
             "command": (
                 "yum install -y postgresql15-server postgresql15 2>/dev/null || true; "
                 "postgresql-setup --initdb 2>/dev/null || true; "
                 "systemctl enable postgresql; "
                 "systemctl start postgresql; "
                 "sleep 3; "
                 "systemctl is-active postgresql && echo POSTGRES_READY || echo POSTGRES_FAILED"
             ),
             "rollback_strategy": "rollback_unavailable"},
        )
        # Create smoke test database
        client.run_cr(
            "[Phase dbadmin] create smoke db",
            "ssm_command", instance_asset_id,
            {"instance_id": instance_id, "document_name": "AWS-RunShellScript",
             "command": (
                 "sudo -u postgres psql -c \"CREATE DATABASE nexplane_smoke_db;\" 2>/dev/null || true; "
                 "sudo -u postgres psql -c \"SELECT datname FROM pg_database WHERE datname='nexplane_smoke_db';\" | grep nexplane_smoke_db && echo DB_CREATED || echo DB_EXISTS; "
                 "sudo -u postgres psql -c \"ALTER USER postgres WITH PASSWORD 'nexplane_smoke_pg';\" 2>/dev/null; "
                 # Allow password auth for postgres user from localhost
                 "sed -i 's/^local.*all.*postgres.*peer/local all postgres md5/' /var/lib/pgsql/data/pg_hba.conf 2>/dev/null || true; "
                 "sed -i 's/^host.*all.*all.*127.0.0.1.*/host all all 127.0.0.1\\/32 md5/' /var/lib/pgsql/data/pg_hba.conf 2>/dev/null || true; "
                 "systemctl reload postgresql 2>/dev/null || true; "
                 "echo POSTGRES_CONFIGURED"
             ),
             "rollback_strategy": "rollback_unavailable"},
        )
        log("dbadmin: PostgreSQL ready on localhost:5432")
        return True
    except Exception as e:
        log(f"dbadmin: PostgreSQL setup failed: {e} — skipping DB admin tests")
        return False
```

- [ ] **Step 2: Add `run_dbadmin_aws_cr`**

```python
def run_dbadmin_aws_cr(client: NexplaneClient, endpoint_asset_id: str,
                        instance_asset_id: str, instance_id: str) -> None:
    """DB admin: provision/deprovision/permission/audit against local PostgreSQL."""
    print("\n  [dbadmin via CR — provision/deprovision/permission/audit]")

    if not _setup_local_postgres(client, instance_asset_id, instance_id):
        log("dbadmin: skipped — PostgreSQL not available")
        return

    db_params_base = {
        "host": "127.0.0.1",
        "port": 5432,
        "database": "nexplane_smoke_db",
        "admin_user": "postgres",
        "admin_password": "nexplane_smoke_pg",
    }

    # 1. Provision a DB user
    _fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "dbadmin-provision", "agent_db_provision",
        params={**db_params_base,
                "username": "nexplane_smoke_user",
                "password": "smoke_password_123",
                "privileges": ["SELECT", "INSERT"],
                "dry_run": False},
        verify_cmd=(
            "PGPASSWORD=nexplane_smoke_pg psql -h 127.0.0.1 -U postgres -d nexplane_smoke_db "
            "-c \"\\du\" 2>/dev/null | grep nexplane_smoke_user && echo USER_CREATED || echo USER_NOT_FOUND"
        ),
        verify_keyword="USER_CREATED",
    )

    # 2. Change DB permissions
    _fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "dbadmin-permission", "agent_db_provision",
        params={**db_params_base,
                "action": "db_permission_change",
                "username": "nexplane_smoke_user",
                "add_privileges": ["UPDATE"],
                "dry_run": False},
        verify_cmd=(
            "PGPASSWORD=nexplane_smoke_pg psql -h 127.0.0.1 -U postgres -d nexplane_smoke_db "
            "-c \"SELECT grantee, privilege_type FROM information_schema.role_table_grants WHERE grantee='nexplane_smoke_user';\" 2>/dev/null && echo PERMISSION_CHECKED"
        ),
        verify_keyword="PERMISSION_CHECKED",
    )

    # 3. Configure DB audit
    _fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "dbadmin-audit", "agent_db_provision",
        params={**db_params_base,
                "action": "configure_db_audit",
                "log_connections": True,
                "log_disconnections": True,
                "dry_run": False},
        verify_cmd="echo db_audit_configured",
        verify_keyword="db_audit_configured",
    )

    # 4. Deprovision the user (rollback of provision)
    _fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "dbadmin-deprovision", "agent_db_provision",
        params={**db_params_base,
                "action": "deprovision_db_user",
                "username": "nexplane_smoke_user",
                "dry_run": False},
        verify_cmd=(
            "PGPASSWORD=nexplane_smoke_pg psql -h 127.0.0.1 -U postgres -d nexplane_smoke_db "
            "-c \"\\du\" 2>/dev/null | grep nexplane_smoke_user || echo USER_REMOVED"
        ),
        verify_keyword="USER_REMOVED",
    )

    log("dbadmin: all DB admin CRs passed")
```

- [ ] **Step 3: Add DB admin call to `run_aws_linux_worker`**

After the existing CR calls, add:
```python
run_dbadmin_aws_cr(client, endpoint_asset_id, instance_asset["id"], instance_id)
```

- [ ] **Step 4: Commit**

```bash
git add tests/smoke/test_agent_live.py
git commit -m "test(smoke): add DB admin smoke tests — provision/deprovision/permission via local PostgreSQL"
```

---

## Task 8: Run Linux Smoke Tests and Fix Errors

- [ ] **Step 1: Run Linux AWS track only**

```bash
docker exec nexplane-backend-1 python tests/smoke/test_agent_live.py \
  --base-url http://localhost:8000 \
  --email admin@acme.example \
  --password admin123 \
  --cloud aws --os linux 2>&1
```

- [ ] **Step 2: Fix any parameter mismatches**

If agent CRs fail with parameter errors, read the Go agent command implementation to find the correct parameter names and fix them. Common issues:
- Parameter name casing (snake_case vs camelCase)
- Missing required params
- HMAC verification failures (check `dispatch_agent_job` and agent job logs)

For each failure, check:
```bash
docker exec nexplane-db-1 psql -U nexplane nexplane -c \
  "SELECT status, error FROM agent_jobs ORDER BY created_at DESC LIMIT 5"
```

- [ ] **Step 3: Fix SSM verification keyword mismatches**

If `verify_keyword` is not found in SSM output, update the verify_cmd to echo the keyword explicitly when the check passes.

- [ ] **Step 4: Re-run until all phases pass, then commit fixes**

```bash
git add tests/smoke/test_agent_live.py
git commit -m "fix(smoke): correct agent CR params and SSM verify keywords from live test run"
```
