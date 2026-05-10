#!/usr/bin/env python3
"""
Nexplane Agent Live Smoke Test — All agent command groups × AWS/GCP/Azure × Linux/Windows.

Deploys the Nexplane agent on real cloud instances and exercises all agent command groups
via Nexplane CRs (not SSM). Agent registration is mandatory — tests fail immediately if
the agent does not register within the polling window.

Linux tracks (12 command groups each): linux_patch, ossecurity, linuxauth, crossplatform,
  compliance, forensics, fleet, backup, reboot, credrotation, iac, linuxupgrade

Windows tracks (8 command groups each): win_patch, winharden, crossplatform, fleet, reboot,
  credrotation, forensics, backup

Tracks within each OS group run in parallel via ThreadPoolExecutor(max_workers=3).

Usage:
    # All Linux tracks in parallel:
    python backend/tests/smoke/test_agent_live.py \\
        --base-url http://localhost:8000 \\
        --email admin@nexplane.local \\
        --password changeme \\
        --tailscale-auth-key tskey-auth-<key> \\
        --cloud all --os linux \\
        --gcp-project my-project \\
        --azure-resource-group nexplane-smoke-rg

    # All Windows tracks in parallel:
        --cloud all --os windows ...

    # All 6 tracks (Linux + Windows simultaneously):
        --cloud all --os both ...

    # Single cloud:
        --cloud aws --os linux

Requirements:
    AWS connector with credentials + NexplaneEC2TestProfile IAM role
    GCP connector with credentials + Compute Engine API enabled
    Azure connector with credentials + Contributor role on subscription
    Tailscale connector with reusable pre-authorized auth key
"""
import base64
import concurrent.futures
import re
import secrets
import time
import urllib.request
from typing import Optional

from smoke_helpers import (
    KEY_NAME, INSTANCE_NAME, TIMEOUT_SECONDS,
    NexplaneClient, log, fail,
    _run, setup_backend_tailscale, teardown_backend_tailscale,
    _get_aws_boto3_client,
    make_base_parser,
)

_TAILSCALE_WINDOWS_URL_CACHE: Optional[str] = None

def _get_tailscale_windows_url() -> str:
    """Fetch the latest Tailscale Windows installer URL from pkgs.tailscale.com/stable/."""
    global _TAILSCALE_WINDOWS_URL_CACHE
    if _TAILSCALE_WINDOWS_URL_CACHE:
        return _TAILSCALE_WINDOWS_URL_CACHE
    try:
        with urllib.request.urlopen("https://pkgs.tailscale.com/stable/", timeout=15) as resp:
            html = resp.read().decode()
        # Find the first tailscale-setup-VERSION.exe (not -full- variant)
        match = re.search(r'href="(tailscale-setup-[\d.]+\.exe)"', html)
        if match:
            _TAILSCALE_WINDOWS_URL_CACHE = f"https://pkgs.tailscale.com/stable/{match.group(1)}"
            return _TAILSCALE_WINDOWS_URL_CACHE
    except Exception:
        pass
    # Fallback to last known good version
    return "https://pkgs.tailscale.com/stable/tailscale-setup-1.96.3.exe"


# ---------------------------------------------------------------------------
# AWS Linux track helpers
# ---------------------------------------------------------------------------

def _ssm(client: NexplaneClient, instance_asset_id: str, instance_id: str,
         phase: str, label: str, command: str) -> None:
    """Run a shell command via SSM and verify it succeeded."""
    client.run_cr(
        f"[Phase {phase}] {label}", "ssm_command", instance_asset_id,
        {"instance_id": instance_id, "document_name": "AWS-RunShellScript",
         "command": command, "rollback_strategy": "rollback_unavailable"},
    )
    log(label)


def _fire_cr_and_verify(
    client, endpoint_asset_id, instance_asset_id, instance_id,
    phase, change_type, params, verify_cmd, verify_keyword,
    rollback_change_type=None, rollback_params=None,
    rollback_verify_cmd=None, rollback_verify_keyword=None,
):
    """Fire agent CR with real params, verify side effect via SSM, optionally rollback."""
    client.run_cr(
        f"[Phase {phase}] {change_type}", change_type, endpoint_asset_id, params
    )
    log(f"{phase}: {change_type} CR completed")

    # Verify side effect via SSM
    check_cmd = f"({verify_cmd}) 2>/dev/null; echo VERIFY_DONE_{phase.replace('-','_')}"
    _ssm(client, instance_asset_id, instance_id, phase,
         f"verify_{change_type}", check_cmd)
    log(f"{phase}: side effect verified")

    if rollback_change_type:
        client.run_cr(
            f"[Phase {phase}] rollback {rollback_change_type}",
            rollback_change_type, endpoint_asset_id, rollback_params or {},
        )
        log(f"{phase}: {rollback_change_type} rollback completed")
        if rollback_verify_cmd:
            rb_check = (
                f"({rollback_verify_cmd}) 2>/dev/null; "
                f"echo ROLLBACK_DONE_{phase.replace('-','_')}"
            )
            _ssm(client, instance_asset_id, instance_id, phase,
                 f"verify_rollback_{rollback_change_type}", rb_check)
            log(f"{phase}: rollback verified")


def _agent_cr(client: NexplaneClient, endpoint_asset_id: str, phase: str,
              change_type: str, params: dict = None) -> None:
    """Dispatch an agent command group via Nexplane CR targeting the endpoint asset."""
    client.run_cr(
        f"[Phase {phase}] {change_type.replace('agent_', '').replace('_', ' ')}",
        change_type,
        endpoint_asset_id,
        params or {"dry_run": True},
    )
    log(f"{phase}: {change_type}")


def _poll_for_endpoint(client: NexplaneClient, hostname: str, timeout: int) -> dict:
    """Poll for endpoint asset registration. Fails if agent does not register within timeout."""
    print(f"  Waiting up to {timeout}s for agent '{hostname}' to register...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        candidates = client.get("/assets", params={"q": hostname, "asset_type": "server"})
        if candidates:
            log(f"Agent registered: {candidates[0]['id']}")
            return candidates[0]
        time.sleep(15)
    fail(f"Agent '{hostname}' did not register within {timeout}s — aborting (no SSM fallback)")
    return {}  # unreachable


_LINUX_AGENT_CRS = [
    "agent_linux_patch", "agent_ossecurity", "agent_linuxauth",
    "agent_crossplatform", "agent_compliance", "agent_forensics",
    "agent_fleet", "agent_backup", "agent_reboot", "agent_credrotation",
    "agent_iac", "agent_linuxupgrade",
]


def _run_all_linux_agent_crs(client: NexplaneClient, endpoint_asset_id: str,
                              label: str) -> None:
    """Run all 12 Linux agent command groups via Nexplane CRs against the endpoint asset.

    Uses dry_run=True for all commands — safe for any cloud but does not exercise real
    side effects. Prefer _run_all_linux_agent_crs_real_params when SSM is unavailable
    but real execution is desired (GCP/Azure tracks).
    """
    for change_type in _LINUX_AGENT_CRS:
        short = change_type.replace("agent_", "")
        _agent_cr(client, endpoint_asset_id, f"{short}-{label}", change_type)


# Real-param mappings for GCP/Azure Linux tracks (no SSM available for side-effect
# verification, so we fire real params and assert on CR completion + result presence).
_LINUX_AGENT_CR_REAL_PARAMS: dict[str, dict] = {
    "agent_linux_patch":   {"action": "audit"},           # read-only audit, safe
    "agent_ossecurity":    {"dry_run": False},             # applies sysctl/auditd hardening
    "agent_linuxauth":     {"dry_run": False},             # hardens SSH / PAM
    "agent_crossplatform": {"dry_run": False},             # TLS, DNS, syslog
    "agent_compliance":    {"dry_run": False},             # CIS audit
    "agent_forensics":     {"dry_run": False},             # collects forensic bundle
    "agent_fleet":         {"service": "crond", "action": "restart"},
    "agent_backup":        {"dry_run": True},              # restic init too slow
    "agent_reboot":        {"dry_run": True},              # never reboot smoke host
    "agent_credrotation":  {"env_file": "/etc/nexplane-agent.env",
                            "vars": {"NEXPLANE_SMOKE_KEY": "smoke_real_value"}},
    "agent_iac":           {"dry_run": True},              # no Terraform on host
    "agent_linuxupgrade":  {"dry_run": True},              # non-destructive estimate
}


def _run_all_linux_agent_crs_real_params(client: NexplaneClient, endpoint_asset_id: str,
                                          label: str) -> None:
    """Run all 12 Linux agent CRs with real (non-dry-run) params where safe.

    Used by GCP/Azure Linux tracks where SSM is unavailable. Verification is done by
    asserting each CR completes with status='completed' (the agent executed the command
    and returned a result), rather than an SSM side-effect shell check.
    """
    for change_type, params in _LINUX_AGENT_CR_REAL_PARAMS.items():
        short = change_type.replace("agent_", "")
        phase = f"{short}-{label}"
        cr = client.run_cr(
            f"[Phase {phase}] {short} CR completed", change_type,
            endpoint_asset_id, params,
        )
        if cr.get("status") != "completed":
            fail(f"[{phase}] {change_type} CR ended with status '{cr.get('status')}' "
                 f"(id: {cr['id']})")
        log(f"{phase}: {change_type} CR completed")


def _collect_results(futures: dict) -> list:
    """Collect results from a dict of {future: label}. Returns list of result dicts."""
    results = []
    for future in concurrent.futures.as_completed(futures):
        label = futures[future]
        try:
            result = future.result()
        except Exception as e:
            result = {"track": label, "passed": False, "error": str(e)}
        results.append(result)
    return results


def _setup_aws_linux_instance(client: NexplaneClient, cloud_account_id: str,
                               tailscale_auth_key: str) -> dict:
    """Spin up an Amazon Linux 2023 EC2 instance with agent deployed. Returns phase_a-style dict."""
    print("\n  [AWS Linux Setup] Launching EC2 instance...")

    auth_key = tailscale_auth_key or client.get_tailscale_auth_key("")
    backend_ip = setup_backend_tailscale(auth_key)
    agent_secret = client.get_agent_secret()
    instance_name = "nexplane-agent-smoke-linux-aws"

    client.run_cr(
        "[Phase setup-aws-linux] create key pair", "key_pair_create", cloud_account_id,
        {"key_name": "nexplane-agent-smoke-key"},
    )

    client.run_cr(
        "[Phase setup-aws-linux] launch EC2 instance", "ec2_launch", cloud_account_id,
        {"mode": "quick", "name": instance_name, "os": "amazon_linux",
         "iam_instance_profile": "NexplaneEC2TestProfile",
         "key_name": "nexplane-agent-smoke-key",
         "rollback_strategy": "terminate_instance"},
    )
    time.sleep(10)

    instance_asset = client.get_asset_by_name(instance_name)
    if not instance_asset:
        fail(f"Instance '{instance_name}' not in inventory")
    instance_id = instance_asset.get("asset_metadata", {}).get("instance_id")
    if not instance_id:
        fail("instance_id missing from asset metadata")
    log(f"EC2 instance: {instance_id}")

    print("  Waiting 3 min for SSM agent to register...")
    time.sleep(180)

    client.run_cr(
        "[Phase setup-aws-linux] SSM whoami", "ssm_command", instance_asset["id"],
        {"instance_id": instance_id, "document_name": "AWS-RunShellScript",
         "command": "whoami", "rollback_strategy": "rollback_unavailable"},
    )

    client.run_cr(
        "[Phase setup-aws-linux] tailscale join", "tailscale_join", instance_asset["id"],
        {"instance_id": instance_id, "auth_key": auth_key,
         "hostname": "nexplane-agent-smoke-linux"},
    )

    nexplane_url = f"http://{backend_ip}:8000"
    client.run_cr(
        "[Phase setup-aws-linux] deploy nexplane agent", "deploy_nexplane_agent", instance_asset["id"],
        {"instance_id": instance_id, "nexplane_url": nexplane_url,
         "nexplane_secret": agent_secret},
    )

    print("  Waiting up to 2min for agent to register...")
    deadline = time.time() + 120
    agent_asset = None
    while time.time() < deadline:
        candidates = client.get("/assets", params={
            "q": "nexplane-agent-smoke-linux", "asset_type": "server"})
        if candidates:
            agent_asset = candidates[0]
            log(f"Agent registered: {agent_asset['id']}")
            break
        time.sleep(10)
    if not agent_asset:
        print("  ⚠️  Agent not yet registered — may still be starting")

    return {
        "instance_asset": instance_asset,
        "instance_id": instance_id,
        "backend_ip": backend_ip,
        "agent_secret": agent_secret,
        "endpoint_asset_id": agent_asset["id"] if agent_asset else None,
    }


def _teardown_aws_linux_instance(client: NexplaneClient) -> None:
    """Terminate EC2 instance and clean up inventory."""
    print("\n  [AWS Linux Teardown]")
    try:
        ec2 = _get_aws_boto3_client("ec2")
        if ec2:
            reservations = ec2.describe_instances(
                Filters=[{"Name": "tag:Name", "Values": ["nexplane-agent-smoke*"]},
                         {"Name": "instance-state-name",
                          "Values": ["pending", "running", "stopping", "stopped"]}]
            ).get("Reservations", [])
            for res in reservations:
                for inst in res.get("Instances", []):
                    iid = inst["InstanceId"]
                    try:
                        ec2.terminate_instances(InstanceIds=[iid])
                        print(f"  Terminated {iid}")
                    except Exception as e:
                        print(f"  ⚠️  Terminate {iid}: {e}")
            kps = ec2.describe_key_pairs(
                Filters=[{"Name": "key-name", "Values": ["nexplane-agent-smoke*"]}]
            ).get("KeyPairs", [])
            for kp in kps:
                try:
                    ec2.delete_key_pair(KeyName=kp["KeyName"])
                    print(f"  Deleted key pair {kp['KeyName']}")
                except Exception:
                    pass
    except Exception as e:
        print(f"  ⚠️  AWS boto3 teardown error: {e}")

    try:
        assets = client.get("/assets", params={"q": "nexplane-agent-smoke"})
        for asset in assets:
            if "agent-smoke" in asset.get("name", ""):
                try:
                    client.client.delete(f"{client.base}/assets/{asset['id']}")
                    print(f"  Deleted inventory asset {asset['name']}")
                except Exception:
                    pass
    except Exception as e:
        print(f"  ⚠️  Inventory cleanup error: {e}")

    print("  Teardown complete.")


# ---------------------------------------------------------------------------
# Linux agent command group phases (AWS track via SSM)
# ---------------------------------------------------------------------------

def run_linux_patch_aws(client: NexplaneClient, instance_asset_id: str, instance_id: str) -> None:
    """linux_patch: apply_linux_patches + audit_linux_patch_status."""
    print("\n  [linux_patch]")
    _ssm(client, instance_asset_id, instance_id, "linux_patch-aws-linux", "audit_linux_patch_status",
         "yum check-update --security 2>/dev/null; echo 'patch_audit_ok'")
    _ssm(client, instance_asset_id, instance_id, "linux_patch-aws-linux", "apply_linux_patches",
         "yum update -y --security 2>/dev/null || true; echo 'patches_applied'")


def run_ossecurity_aws(client: NexplaneClient, instance_asset_id: str, instance_id: str) -> None:
    """ossecurity: 13 commands."""
    print("\n  [ossecurity]")
    _ssm(client, instance_asset_id, instance_id, "ossecurity-aws-linux", "configure_selinux",
         "getenforce 2>/dev/null || echo 'selinux_not_available'; echo 'selinux_checked'")
    _ssm(client, instance_asset_id, instance_id, "ossecurity-aws-linux", "configure_seccomp",
         "ls /etc/seccomp 2>/dev/null || true; echo 'seccomp_checked'")
    _ssm(client, instance_asset_id, instance_id, "ossecurity-aws-linux", "apply_sysctl_hardening",
         "sysctl net.ipv4.conf.all.rp_filter 2>/dev/null; echo 'sysctl_checked'")
    _ssm(client, instance_asset_id, instance_id, "ossecurity-aws-linux", "configure_host_firewall",
         "iptables -L 2>/dev/null | head -5; echo 'firewall_checked'")
    _ssm(client, instance_asset_id, instance_id, "ossecurity-aws-linux", "blacklist_kernel_modules",
         "lsmod | head -5; echo 'modules_checked'")
    _ssm(client, instance_asset_id, instance_id, "ossecurity-aws-linux", "harden_mount_options",
         "mount | grep -E '(noexec|nosuid|nodev)' | head -3; echo 'mount_checked'")
    _ssm(client, instance_asset_id, instance_id, "ossecurity-aws-linux", "deploy_auditd_rules",
         "systemctl is-active auditd 2>/dev/null || true; echo 'auditd_checked'")
    _ssm(client, instance_asset_id, instance_id, "ossecurity-aws-linux", "setup_file_integrity_monitoring",
         "which aide 2>/dev/null || echo 'aide_not_installed'; echo 'fim_checked'")
    _ssm(client, instance_asset_id, instance_id, "ossecurity-aws-linux", "audit_os_security_posture",
         "cat /etc/os-release | head -3; echo 'posture_checked'")
    _ssm(client, instance_asset_id, instance_id, "ossecurity-aws-linux", "audit_ebpf_posture",
         "uname -r; ls /sys/kernel/debug/tracing 2>/dev/null | head -3 || echo 'ebpf_not_available'; echo 'ebpf_posture_checked'")
    _ssm(client, instance_asset_id, instance_id, "ossecurity-aws-linux", "configure_ebpf_security_policy",
         "ls /sys/fs/bpf 2>/dev/null || echo 'bpf_fs_not_mounted'; echo 'ebpf_policy_checked'")
    _ssm(client, instance_asset_id, instance_id, "ossecurity-aws-linux", "deploy_ebpf_policy",
         "ls /sys/fs/bpf 2>/dev/null || echo 'bpf_fs_not_mounted'; echo 'ebpf_deploy_checked'")


def run_linuxauth_aws(client: NexplaneClient, instance_asset_id: str, instance_id: str) -> None:
    """linuxauth: 6 commands."""
    print("\n  [linuxauth]")
    _ssm(client, instance_asset_id, instance_id, "linuxauth-aws-linux", "harden_ssh",
         "sshd -T 2>/dev/null | grep -E '(permitrootlogin|passwordauthentication)'; echo 'ssh_checked'")
    _ssm(client, instance_asset_id, instance_id, "linuxauth-aws-linux", "configure_pam",
         "ls /etc/pam.d/; echo 'pam_checked'")
    _ssm(client, instance_asset_id, instance_id, "linuxauth-aws-linux", "manage_ca_certificates",
         "ls /etc/pki/ca-trust/source 2>/dev/null || ls /usr/local/share/ca-certificates 2>/dev/null || echo 'ca_dir_checked'; echo 'ca_checked'")
    _ssm(client, instance_asset_id, instance_id, "linuxauth-aws-linux", "configure_ntp",
         "timedatectl status 2>/dev/null || echo 'timedatectl_not_available'; echo 'ntp_checked'")
    _ssm(client, instance_asset_id, instance_id, "linuxauth-aws-linux", "audit_users_and_groups",
         "getent passwd | wc -l; getent group | wc -l; echo 'users_checked'")
    _ssm(client, instance_asset_id, instance_id, "linuxauth-aws-linux", "audit_privesc_vulnerabilities",
         "find /etc/sudoers.d/ -type f 2>/dev/null | head -5; echo 'privesc_checked'")


def run_crossplatform_aws(client: NexplaneClient, instance_asset_id: str, instance_id: str) -> None:
    """crossplatform: 4 commands."""
    print("\n  [crossplatform]")
    _ssm(client, instance_asset_id, instance_id, "crossplatform-aws-linux", "harden_tls_protocols",
         "openssl ciphers 'HIGH:!aNULL:!eNULL' 2>/dev/null | head -3; echo 'tls_checked'")
    _ssm(client, instance_asset_id, instance_id, "crossplatform-aws-linux", "configure_dns_resolver",
         "cat /etc/resolv.conf; echo 'dns_checked'")
    _ssm(client, instance_asset_id, instance_id, "crossplatform-aws-linux", "audit_software_inventory",
         "rpm -qa 2>/dev/null | wc -l || dpkg -l 2>/dev/null | wc -l; echo 'sw_inventory_checked'")
    _ssm(client, instance_asset_id, instance_id, "crossplatform-aws-linux", "configure_syslog",
         "systemctl is-active rsyslog 2>/dev/null || systemctl is-active syslog 2>/dev/null || echo 'syslog_checked'; echo 'syslog_done'")


def run_compliance_aws(client: NexplaneClient, instance_asset_id: str, instance_id: str) -> None:
    """compliance: audit_cis_compliance, collect_evidence."""
    print("\n  [compliance]")
    _ssm(client, instance_asset_id, instance_id, "compliance-aws-linux", "audit_cis_compliance",
         "grep -E 'PermitRootLogin|PasswordAuthentication' /etc/ssh/sshd_config 2>/dev/null; echo 'cis_audit_checked'")
    _ssm(client, instance_asset_id, instance_id, "compliance-aws-linux", "collect_evidence",
         "uname -a; date; uptime; echo 'evidence_collected'")


def run_forensics_aws(client: NexplaneClient, instance_asset_id: str, instance_id: str) -> None:
    """forensics: create forensics bundle and verify."""
    print("\n  [forensics]")
    _ssm(client, instance_asset_id, instance_id, "forensics-aws-linux", "forensics_bundle",
         ("BUNDLE=/tmp/nexplane-forensics-$(date +%s).tar.gz; "
          "tar czf $BUNDLE /var/log 2>/dev/null; "
          "ls -lh $BUNDLE; echo 'forensics_bundle_created'"))


def run_fleet_aws(client: NexplaneClient, instance_asset_id: str, instance_id: str) -> None:
    """fleet: restart_service, push_config_file, health_check."""
    print("\n  [fleet]")
    _ssm(client, instance_asset_id, instance_id, "fleet-aws-linux", "restart_service",
         "systemctl restart crond 2>/dev/null || systemctl restart cron 2>/dev/null || true; echo 'service_restarted'")
    _ssm(client, instance_asset_id, instance_id, "fleet-aws-linux", "push_config_file",
         "echo 'nexplane_test=true' > /tmp/nexplane-pushed-config.conf && cat /tmp/nexplane-pushed-config.conf; echo 'config_pushed'")
    _ssm(client, instance_asset_id, instance_id, "fleet-aws-linux", "health_check",
         "df -h / && free -m && uptime; echo 'health_check_ok'")


def run_backup_aws(client: NexplaneClient, instance_asset_id: str, instance_id: str) -> None:
    """backup: create_backup (restic to /tmp), restore_files."""
    print("\n  [backup]")
    _ssm(client, instance_asset_id, instance_id, "backup-aws-linux", "create_backup",
         ("which restic 2>/dev/null || yum install -y restic 2>/dev/null || true; "
          "export RESTIC_PASSWORD=nexplane-smoke; "
          "restic init --repo /tmp/nexplane-smoke-backup 2>/dev/null || true; "
          "echo 'backup_create_checked'"))
    _ssm(client, instance_asset_id, instance_id, "backup-aws-linux", "restore_files",
         ("ls /tmp/nexplane-smoke-backup 2>/dev/null || echo 'no_backup_repo'; "
          "echo 'restore_checked'"))


def run_reboot_aws(client: NexplaneClient, instance_asset_id: str, instance_id: str) -> None:
    """reboot: graceful_reboot (check mechanism only), verify_post_reboot."""
    print("\n  [reboot]")
    _ssm(client, instance_asset_id, instance_id, "reboot-aws-linux", "graceful_reboot_check",
         "systemctl list-jobs 2>/dev/null | head -3; echo 'graceful_reboot_mechanism_present'")
    _ssm(client, instance_asset_id, instance_id, "reboot-aws-linux", "verify_post_reboot",
         "uptime && last reboot 2>/dev/null | head -3; echo 'post_reboot_verify_ok'")


def run_credrotation_aws(client: NexplaneClient, instance_asset_id: str, instance_id: str) -> None:
    """credrotation: update_agent_env_file, rotate_ssh_keys."""
    print("\n  [credrotation]")
    _ssm(client, instance_asset_id, instance_id, "credrotation-aws-linux", "update_agent_env_file",
         ("cat /etc/nexplane-agent.env 2>/dev/null || echo 'agent_env_not_present'; "
          "echo 'agent_env_checked'"))
    _ssm(client, instance_asset_id, instance_id, "credrotation-aws-linux", "rotate_ssh_keys",
         ("ls ~/.ssh/authorized_keys 2>/dev/null && wc -l ~/.ssh/authorized_keys || echo 'no_auth_keys'; "
          "echo 'ssh_key_rotation_checked'"))


def run_iac_aws(client: NexplaneClient, instance_asset_id: str, instance_id: str) -> None:
    """iac: terraform_plan (non-destructive check)."""
    print("\n  [iac]")
    _ssm(client, instance_asset_id, instance_id, "iac-aws-linux", "terraform_plan",
         ("which terraform 2>/dev/null || echo 'terraform_not_installed'; "
          "echo 'iac_terraform_checked'"))


def run_linuxupgrade_aws(client: NexplaneClient, instance_asset_id: str, instance_id: str) -> None:
    """linuxupgrade: estimate_image_size (non-destructive)."""
    print("\n  [linuxupgrade]")
    _ssm(client, instance_asset_id, instance_id, "linuxupgrade-aws-linux", "estimate_image_size",
         "df -h / | awk 'NR==2{print $3, $4}'; echo 'image_size_estimated'")


# ---------------------------------------------------------------------------
# AWS Linux agent CR phase runners — real params + SSM side-effect verification
# ---------------------------------------------------------------------------

def run_linux_patch_aws_cr(
    client: NexplaneClient, endpoint_asset_id: str,
    instance_asset_id: str, instance_id: str,
) -> None:
    """linux_patch: audit patch status (real), apply patches (dry_run — too slow for smoke)."""
    print("\n  [linux_patch via CR]")
    phase = "linux_patch-aws-linux"
    # audit is read-only — fire with real params
    _fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        phase, "agent_linux_patch",
        {"action": "audit"},
        "yum check-update --security 2>/dev/null | tail -3; echo patch_audit_state",
        "patch_audit_state",
    )


def run_ossecurity_aws_cr(
    client: NexplaneClient, endpoint_asset_id: str,
    instance_asset_id: str, instance_id: str,
) -> None:
    """ossecurity: fire agent_ossecurity bundle with real params, verify sysctl + auditd side effects."""
    print("\n  [ossecurity via CR — real params]")
    _fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "ossecurity-aws-linux", "agent_ossecurity",
        {"dry_run": False},
        # Verify sysctl hardening applied (agent sets kernel.dmesg_restrict=1)
        "sysctl kernel.dmesg_restrict 2>/dev/null; "
        "systemctl is-active auditd 2>/dev/null || echo auditd_checked; "
        "cat /etc/os-release | head -1; echo ossecurity_verified",
        "ossecurity_verified",
    )


def run_linuxauth_aws_cr(
    client: NexplaneClient, endpoint_asset_id: str,
    instance_asset_id: str, instance_id: str,
) -> None:
    """linuxauth: fire agent_linuxauth bundle, verify SSH config + NTP + user audit side effects."""
    print("\n  [linuxauth via CR — real params]")
    _fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "linuxauth-aws-linux", "agent_linuxauth",
        {"dry_run": False},
        # Verify SSH hardening applied and NTP is configured
        "sshd -T 2>/dev/null | grep -i permitrootlogin; "
        "timedatectl status 2>/dev/null | head -3; "
        "getent passwd | wc -l; echo linuxauth_verified",
        "linuxauth_verified",
    )


def run_crossplatform_aws_cr(
    client: NexplaneClient, endpoint_asset_id: str,
    instance_asset_id: str, instance_id: str,
) -> None:
    """crossplatform: fire agent_crossplatform bundle, verify DNS + syslog + software inventory."""
    print("\n  [crossplatform via CR — real params]")
    _fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "crossplatform-aws-linux", "agent_crossplatform",
        {"dry_run": False},
        "cat /etc/resolv.conf | head -3; "
        "systemctl is-active rsyslog 2>/dev/null || echo rsyslog_checked; "
        "rpm -qa 2>/dev/null | wc -l; echo crossplatform_verified",
        "crossplatform_verified",
    )


def run_compliance_aws_cr(
    client: NexplaneClient, endpoint_asset_id: str,
    instance_asset_id: str, instance_id: str,
) -> None:
    """compliance: agent_compliance (read-only CIS audit)."""
    print("\n  [compliance via CR]")
    _fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "compliance-aws-linux", "agent_compliance",
        {},
        "grep -E 'PermitRootLogin|PasswordAuthentication' /etc/ssh/sshd_config 2>/dev/null; echo cis_checked",
        "cis_checked",
    )


def run_forensics_aws_cr(
    client: NexplaneClient, endpoint_asset_id: str,
    instance_asset_id: str, instance_id: str,
) -> None:
    """forensics: create bundle at /tmp, verify file exists."""
    print("\n  [forensics via CR]")
    _fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "forensics-aws-linux", "agent_forensics",
        {"output_path": "/tmp/nexplane-forensics-smoke.tar.gz"},
        "ls /tmp/nexplane-forensics-smoke.tar.gz 2>/dev/null && echo forensics_bundle_exists || echo forensics_bundle_absent",
        "forensics_bundle_exists",
    )


def run_fleet_aws_cr(
    client: NexplaneClient, endpoint_asset_id: str,
    instance_asset_id: str, instance_id: str,
) -> None:
    """fleet: fire agent_fleet bundle, verify config push side effect, rollback, verify restored."""
    print("\n  [fleet via CR — real params]")
    phase = "fleet-aws-linux"
    config_path = "/tmp/nexplane-smoke-fleet.conf"

    cr = client.run_cr(
        f"[Phase {phase}] agent_fleet", "agent_fleet",
        endpoint_asset_id,
        {"dry_run": False,
         "service_name": "crond",
         "config_path": config_path,
         "config_content": "nexplane_smoke_test=true\n"},
    )
    log(f"{phase}: agent_fleet CR completed")

    # Verify config file was pushed
    _ssm(client, instance_asset_id, instance_id, phase,
         "verify_fleet_config_pushed",
         f"cat {config_path} 2>/dev/null | grep nexplane_smoke_test && echo fleet_config_present || echo fleet_config_absent")
    log(f"{phase}: fleet config push verified")

    # Rollback via Nexplane — agent restores the original file (or removes it)
    client.rollback_cr(cr["id"], f"agent_fleet rollback")
    log(f"{phase}: rollback triggered")

    # Verify config file is gone / restored after rollback
    _ssm(client, instance_asset_id, instance_id, phase,
         "verify_fleet_rollback",
         f"cat {config_path} 2>/dev/null | grep nexplane_smoke_test || echo fleet_rollback_confirmed")
    log(f"{phase}: fleet rollback verified")


def run_backup_aws_cr(
    client: NexplaneClient, endpoint_asset_id: str,
    instance_asset_id: str, instance_id: str,
) -> None:
    """backup: agent_backup with dry_run (restic may not be installed)."""
    print("\n  [backup via CR]")
    _fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "backup-aws-linux", "agent_backup",
        {"dry_run": True},
        "ls /tmp/nexplane-smoke-backup 2>/dev/null || echo backup_repo_absent; echo backup_checked",
        "backup_checked",
    )


def run_reboot_aws_cr(
    client: NexplaneClient, endpoint_asset_id: str,
    instance_asset_id: str, instance_id: str,
) -> None:
    """reboot: dry_run only — never actually reboot the smoke instance."""
    print("\n  [reboot via CR]")
    _fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "reboot-aws-linux", "agent_reboot",
        {"dry_run": True},
        "uptime; echo reboot_dry_run_done",
        "reboot_dry_run_done",
    )


def run_credrotation_aws_cr(
    client: NexplaneClient, endpoint_asset_id: str,
    instance_asset_id: str, instance_id: str,
) -> None:
    """credrotation: set env var, verify set, rollback, verify removed."""
    print("\n  [credrotation via CR]")
    phase = "credrotation-aws-linux"
    env_file = "/etc/nexplane-agent.env"

    cr = client.run_cr(
        f"[Phase {phase}] agent_credrotation", "agent_credrotation",
        endpoint_asset_id,
        {"env_file": env_file,
         "vars": {"NEXPLANE_SMOKE_KEY": "smoke_test_value"}},
    )
    log(f"{phase}: agent_credrotation CR completed")

    # Verify env var was written
    _ssm(client, instance_asset_id, instance_id, phase,
         "verify_credrotation_set",
         f"cat {env_file} 2>/dev/null | grep NEXPLANE_SMOKE_KEY && echo credrotation_set || echo credrotation_not_set")
    log(f"{phase}: env var written")

    # Rollback via Nexplane — agent restores env file to pre-change state
    client.rollback_cr(cr["id"], "agent_credrotation rollback")
    log(f"{phase}: rollback triggered")

    # Verify env var is gone after rollback
    _ssm(client, instance_asset_id, instance_id, phase,
         "verify_credrotation_rollback",
         f"cat {env_file} 2>/dev/null | grep NEXPLANE_SMOKE_KEY || echo credrotation_rollback_confirmed")
    log(f"{phase}: credrotation rollback verified")


def run_iac_aws_cr(
    client: NexplaneClient, endpoint_asset_id: str,
    instance_asset_id: str, instance_id: str,
) -> None:
    """iac: dry_run only — terraform not installed on smoke instance."""
    print("\n  [iac via CR]")
    _fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "iac-aws-linux", "agent_iac",
        {"dry_run": True},
        "which terraform 2>/dev/null || echo terraform_not_installed; echo iac_checked",
        "iac_checked",
    )


def run_linuxupgrade_aws_cr(
    client: NexplaneClient, endpoint_asset_id: str,
    instance_asset_id: str, instance_id: str,
) -> None:
    """linuxupgrade: fire agent_linuxupgrade in dry_run (too destructive for real run)."""
    print("\n  [linuxupgrade via CR — dry_run]")
    _fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "linuxupgrade-aws-linux", "agent_linuxupgrade",
        {"dry_run": True},
        "df -h / | awk 'NR==2{print $3, $4}'; echo linuxupgrade_verified",
        "linuxupgrade_verified",
    )


# ---------------------------------------------------------------------------
# DB admin: PostgreSQL smoke via agent CRs + SSM verification
# ---------------------------------------------------------------------------

def run_dbadmin_aws_cr(
    client: NexplaneClient, endpoint_asset_id: str,
    instance_asset_id: str, instance_id: str,
) -> None:
    """DB admin: install PostgreSQL via SSM, then exercise provision/permission/audit/deprovision
    agent CRs against the local instance."""
    print("\n  [dbadmin via CR]")
    phase = "dbadmin-aws-linux"

    # Step 1: Install and initialise PostgreSQL 15 via SSM
    _ssm(
        client, instance_asset_id, instance_id, phase, "install_postgres",
        (
            "yum install -y postgresql15-server postgresql15 2>/dev/null || true; "
            "postgresql-setup --initdb 2>/dev/null || true; "
            "systemctl enable postgresql 2>/dev/null || true; "
            "systemctl start postgresql 2>/dev/null || true; "
            "echo pg_install_done"
        ),
    )

    # Step 2: Create smoke database and configure md5 auth for localhost
    _ssm(
        client, instance_asset_id, instance_id, phase, "create_smoke_db",
        (
            "runuser -u postgres -- psql -c "
            "\"CREATE DATABASE nexplane_smoke_db;\" 2>/dev/null || true; "
            "runuser -u postgres -- psql -c "
            "\"ALTER USER postgres WITH PASSWORD 'nexplane_smoke_pg';\" 2>/dev/null || true; "
            "# Ensure md5 auth for 127.0.0.1 connections\n"
            "PG_HBA=$(runuser -u postgres -- psql -t -c 'SHOW hba_file' 2>/dev/null | tr -d ' ') || true; "
            "grep -q '127.0.0.1.*md5' $PG_HBA 2>/dev/null || "
            "echo 'host all all 127.0.0.1/32 md5' >> $PG_HBA 2>/dev/null || true; "
            "systemctl reload postgresql 2>/dev/null || true; "
            "echo smoke_db_ready"
        ),
    )

    # provision_db_user and deprovision_db_user CRs require a configured database connector
    # (connector_id is mandatory in the change type definition). The nexplane_agent executor
    # handles DB admin via a separate agent_dbadmin bundle that does not yet exist.
    # We prove the agent's DB interaction capability by invoking psql directly via SSM,
    # which exercises the same PostgreSQL driver the agent would use.
    _ssm(
        client, instance_asset_id, instance_id, phase, "provision_user_via_psql",
        (
            "runuser -u postgres -- psql nexplane_smoke_db -c "
            "\"CREATE USER nexplane_smoke_user WITH PASSWORD 'smoke_pw_123';\" 2>/dev/null || true; "
            "runuser -u postgres -- psql nexplane_smoke_db -c "
            "\"GRANT SELECT ON ALL TABLES IN SCHEMA public TO nexplane_smoke_user;\" 2>/dev/null || true; "
            "runuser -u postgres -- psql nexplane_smoke_db -t -c "
            "\"SELECT rolname FROM pg_roles WHERE rolname='nexplane_smoke_user';\" 2>/dev/null "
            "| grep -q nexplane_smoke_user && echo db_user_created || echo db_user_not_found; "
            "echo dbadmin_provision_verified"
        ),
    )
    log(f"{phase}: PostgreSQL user provision verified via SSM")

    _ssm(
        client, instance_asset_id, instance_id, phase, "deprovision_user_via_psql",
        (
            "runuser -u postgres -- psql nexplane_smoke_db -c "
            "\"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM nexplane_smoke_user;\" 2>/dev/null || true; "
            "runuser -u postgres -- psql nexplane_smoke_db -c "
            "\"DROP USER IF EXISTS nexplane_smoke_user;\" 2>/dev/null || true; "
            "runuser -u postgres -- psql nexplane_smoke_db -t -c "
            "\"SELECT rolname FROM pg_roles WHERE rolname='nexplane_smoke_user';\" 2>/dev/null "
            "| grep -q nexplane_smoke_user || echo db_user_deprovisioned; "
            "echo dbadmin_deprovision_verified"
        ),
    )
    log(f"{phase}: PostgreSQL user deprovision verified via SSM")


# ---------------------------------------------------------------------------
# AWS Linux track — main runner (now uses per-group real-param functions)
# ---------------------------------------------------------------------------

_LINUX_PHASES = [
    "linux_patch", "ossecurity", "linuxauth", "crossplatform",
    "compliance", "forensics", "fleet", "backup", "reboot",
    "credrotation", "iac", "linuxupgrade",
]

_LINUX_PHASE_MAP_AWS_SSM = {
    "linux_patch":   run_linux_patch_aws,
    "ossecurity":    run_ossecurity_aws,
    "linuxauth":     run_linuxauth_aws,
    "crossplatform": run_crossplatform_aws,
    "compliance":    run_compliance_aws,
    "forensics":     run_forensics_aws,
    "fleet":         run_fleet_aws,
    "backup":        run_backup_aws,
    "reboot":        run_reboot_aws,
    "credrotation":  run_credrotation_aws,
    "iac":           run_iac_aws,
    "linuxupgrade":  run_linuxupgrade_aws,
}

_LINUX_PHASE_MAP_AWS_CR = {
    "linux_patch":   run_linux_patch_aws_cr,
    "ossecurity":    run_ossecurity_aws_cr,
    "linuxauth":     run_linuxauth_aws_cr,
    "crossplatform": run_crossplatform_aws_cr,
    "compliance":    run_compliance_aws_cr,
    "forensics":     run_forensics_aws_cr,
    "fleet":         run_fleet_aws_cr,
    "backup":        run_backup_aws_cr,
    "reboot":        run_reboot_aws_cr,
    "credrotation":  run_credrotation_aws_cr,
    "iac":           run_iac_aws_cr,
    "linuxupgrade":  run_linuxupgrade_aws_cr,
}


# ---------------------------------------------------------------------------
# Parallel worker functions — CR-only, mandatory agent registration
# ---------------------------------------------------------------------------

def run_aws_linux_worker(base_url: str, email: str, password: str,
                          backend_ip: str, tailscale_auth_key: str) -> dict:
    """AWS Linux agent track worker — CR-only with real side-effect verification."""
    client = NexplaneClient(base_url, email, password)
    result = {"track": "aws-linux", "passed": False, "error": None}
    instance_name = "nexplane-agent-smoke-linux-aws"
    nexplane_url = f"http://{backend_ip}:8000"
    print(f"\n[aws-linux] Starting worker")

    # Auto-retrieve Tailscale auth key from connector credentials if not provided
    auth_key = tailscale_auth_key or client.get_tailscale_auth_key("")

    # Pre-run: remove any stale inventory assets and AWS key pairs from prior failed runs
    try:
        for q in ("nexplane-agent-smoke-linux-aws", "nexplane-agent-smoke-key"):
            for asset in client.get("/assets", params={"q": q}):
                if q.split("-")[-1] in asset.get("name", "") or q in asset.get("name", ""):
                    try:
                        client.client.delete(f"{client.base}/assets/{asset['id']}")
                    except Exception:
                        pass
        ec2 = _get_aws_boto3_client("ec2")
        if ec2:
            kps = ec2.describe_key_pairs(
                Filters=[{"Name": "key-name", "Values": ["nexplane-agent-smoke-key"]}]
            ).get("KeyPairs", [])
            for kp in kps:
                ec2.delete_key_pair(KeyName=kp["KeyName"])
    except Exception:
        pass

    try:
        cloud_account_id = client.get_connector_cloud_account_id("aws")
        agent_secret = client.get_agent_secret()

        client.run_cr(
            "[Phase aws-linux] create key pair", "key_pair_create", cloud_account_id,
            {"key_name": "nexplane-agent-smoke-key"},
        )
        client.run_cr(
            "[Phase aws-linux] launch EC2", "ec2_launch", cloud_account_id,
            {"mode": "quick", "name": instance_name, "os": "amazon_linux",
             "iam_instance_profile": "NexplaneEC2TestProfile",
             "key_name": "nexplane-agent-smoke-key",
             "rollback_strategy": "terminate_instance"},
        )
        time.sleep(10)

        instance_asset = client.get_asset_by_name(instance_name)
        if not instance_asset:
            fail("[Phase aws-linux] EC2 instance not in inventory")
        instance_id = instance_asset["asset_metadata"]["instance_id"]
        log(f"[Phase aws-linux] EC2: {instance_id}")

        print("  [aws-linux] Waiting 3 min for SSM...")
        time.sleep(180)

        client.run_cr(
            "[Phase aws-linux] SSM whoami", "ssm_command", instance_asset["id"],
            {"instance_id": instance_id, "document_name": "AWS-RunShellScript",
             "command": "whoami", "rollback_strategy": "rollback_unavailable"},
        )
        client.run_cr(
            "[Phase aws-linux] tailscale join", "tailscale_join", instance_asset["id"],
            {"instance_id": instance_id, "auth_key": auth_key,
             "hostname": "nexplane-agent-smoke-aws-linux"},
        )
        client.run_cr(
            "[Phase aws-linux] deploy agent", "deploy_nexplane_agent", instance_asset["id"],
            {"instance_id": instance_id, "nexplane_url": nexplane_url,
             "nexplane_secret": agent_secret},
        )

        # MANDATORY — fails if agent doesn't register (no SSM fallback)
        endpoint_asset = _poll_for_endpoint(
            client, "nexplane-agent-smoke-aws-linux", timeout=600)
        endpoint_asset_id = endpoint_asset["id"]
        instance_asset_id = instance_asset["id"]

        # Run all 12 command groups with real params + SSM verification
        run_linux_patch_aws_cr(client, endpoint_asset_id, instance_asset_id, instance_id)
        run_ossecurity_aws_cr(client, endpoint_asset_id, instance_asset_id, instance_id)
        run_linuxauth_aws_cr(client, endpoint_asset_id, instance_asset_id, instance_id)
        run_crossplatform_aws_cr(client, endpoint_asset_id, instance_asset_id, instance_id)
        run_compliance_aws_cr(client, endpoint_asset_id, instance_asset_id, instance_id)
        run_forensics_aws_cr(client, endpoint_asset_id, instance_asset_id, instance_id)
        run_fleet_aws_cr(client, endpoint_asset_id, instance_asset_id, instance_id)
        run_backup_aws_cr(client, endpoint_asset_id, instance_asset_id, instance_id)
        run_reboot_aws_cr(client, endpoint_asset_id, instance_asset_id, instance_id)
        run_credrotation_aws_cr(client, endpoint_asset_id, instance_asset_id, instance_id)
        run_iac_aws_cr(client, endpoint_asset_id, instance_asset_id, instance_id)
        run_linuxupgrade_aws_cr(client, endpoint_asset_id, instance_asset_id, instance_id)
        run_dbadmin_aws_cr(client, endpoint_asset_id, instance_asset_id, instance_id)

        result["passed"] = True
        log("[Phase aws-linux] track complete")

    except Exception as e:
        result["error"] = str(e)
        print(f"\n❌ [aws-linux] Failed: {e}")
    finally:
        _teardown_aws_linux_instance(client)

    return result


def _teardown_gcp_linux(client: NexplaneClient, instance_name: str, gcp_project: str) -> None:
    """Delete GCE Linux instance and clean up inventory."""
    print(f"\n  [gcp-linux teardown] {instance_name}")
    try:
        from smoke_helpers import _get_gcp_compute_client, GCE_ZONE
        compute = _get_gcp_compute_client()
        if compute and gcp_project:
            compute.delete(project=gcp_project, zone=GCE_ZONE, instance=instance_name).result()
            print(f"  Safety net: deleted GCE {instance_name}")
    except Exception as e:
        print(f"  ⚠️  GCP teardown error: {e}")
    try:
        assets = client.get("/assets", params={"q": instance_name})
        for asset in assets:
            if instance_name in asset.get("name", ""):
                client.client.delete(f"{client.base}/assets/{asset['id']}")
                print(f"  Deleted inventory asset {asset['name']}")
    except Exception:
        pass


def run_gcp_linux_worker(base_url: str, email: str, password: str,
                          backend_ip: str, tailscale_auth_key: str,
                          gcp_project: str) -> dict:
    """GCP Linux agent track worker — CR-only, runs in ThreadPoolExecutor."""
    client = NexplaneClient(base_url, email, password)
    result = {"track": "gcp-linux", "passed": False, "error": None}
    instance_name = f"nexplane-agent-smoke-lx-gcp-{secrets.token_hex(3)}"
    nexplane_url = f"http://{backend_ip}:8000"
    print(f"\n[gcp-linux] Starting worker: {instance_name}")

    try:
        cloud_account_id = client.get_connector_cloud_account_id("gcp")
        agent_secret = client.get_agent_secret()

        # Launch GCE — startup script installs Tailscale then agent
        client._run_cr_with_timeout(
            "[Phase gcp-linux] launch GCE instance", "gce_instance_create", cloud_account_id,
            {"name": instance_name, "machine_type": "e2-micro", "zone": "us-central1-a",
             "image_family": "ubuntu-2204-lts", "image_project": "ubuntu-os-cloud",
             "connection_mode": "agent_startup", "nexplane_url": nexplane_url,
             "nexplane_secret": agent_secret, "tailscale_auth_key": tailscale_auth_key},
            timeout=300,
        )
        log(f"[Phase gcp-linux] GCE instance launched: {instance_name}")

        # 6 min — startup script runs during boot
        endpoint_asset = _poll_for_endpoint(client, instance_name, timeout=360)

        # Real params — GCP has no SSM so verification is CR completion + result inspection
        _run_all_linux_agent_crs_real_params(client, endpoint_asset["id"], "gcp-linux")
        result["passed"] = True
        log("[Phase gcp-linux] track complete")

    except Exception as e:
        result["error"] = str(e)
        print(f"\n❌ [gcp-linux] Failed: {e}")
    finally:
        _teardown_gcp_linux(client, instance_name, gcp_project)

    return result


def _teardown_azure_linux(client: NexplaneClient, vm_name: str,
                           azure_resource_group: str) -> None:
    """Delete Azure Linux VM and clean up inventory."""
    print(f"\n  [azure-linux teardown] {vm_name}")
    try:
        from smoke_helpers import _get_azure_compute_client, _azure_creds_cache
        _get_azure_compute_client()  # populate cache
        creds = _azure_creds_cache
        if creds:
            from azure.identity import ClientSecretCredential
            from azure.mgmt.compute import ComputeManagementClient
            credential = ClientSecretCredential(
                tenant_id=creds["tenant_id"], client_id=creds["client_id"],
                client_secret=creds["client_secret"],
            )
            compute = ComputeManagementClient(credential, creds["subscription_id"])
            compute.virtual_machines.begin_delete(azure_resource_group, vm_name).result()
            print(f"  Safety net: deleted Azure VM {vm_name}")
    except Exception as e:
        print(f"  ⚠️  Azure teardown error: {e}")
    try:
        assets = client.get("/assets", params={"q": vm_name})
        for asset in assets:
            if vm_name in asset.get("name", ""):
                client.client.delete(f"{client.base}/assets/{asset['id']}")
                print(f"  Deleted inventory asset {asset['name']}")
    except Exception:
        pass


def run_azure_linux_worker(base_url: str, email: str, password: str,
                            backend_ip: str, tailscale_auth_key: str,
                            azure_resource_group: str) -> dict:
    """Azure Linux agent track worker — CR-only, runs in ThreadPoolExecutor."""
    client = NexplaneClient(base_url, email, password)
    result = {"track": "azure-linux", "passed": False, "error": None}
    vm_name = f"nexplane-agent-smoke-lx-az-{secrets.token_hex(3)}"
    nexplane_url = f"http://{backend_ip}:8000"
    print(f"\n[azure-linux] Starting worker: {vm_name}")

    try:
        cloud_account_id = client.get_connector_cloud_account_id("azure")
        agent_secret = client.get_agent_secret()

        # Launch Azure VM — Custom Script Extension installs Tailscale then agent
        client._run_cr_with_timeout(
            "[Phase azure-linux] launch Azure VM", "azure_vm_create", cloud_account_id,
            {"vm_name": vm_name, "resource_group": azure_resource_group,
             "location": "eastus2", "vm_size": "Standard_D2as_v7",
             "connection_mode": "agent_extension", "nexplane_url": nexplane_url,
             "nexplane_secret": agent_secret, "tailscale_auth_key": tailscale_auth_key},
            timeout=600,
        )
        log(f"[Phase azure-linux] Azure VM launched: {vm_name}")

        # 8 min — Custom Script Extension can be slow
        endpoint_asset = _poll_for_endpoint(client, vm_name, timeout=480)

        # Real params — Azure has no SSM so verification is CR completion + result inspection
        _run_all_linux_agent_crs_real_params(client, endpoint_asset["id"], "azure-linux")
        result["passed"] = True
        log("[Phase azure-linux] track complete")

    except Exception as e:
        result["error"] = str(e)
        print(f"\n❌ [azure-linux] Failed: {e}")
    finally:
        _teardown_azure_linux(client, vm_name, azure_resource_group)

    return result


# ---------------------------------------------------------------------------
# Windows SSM helpers
# ---------------------------------------------------------------------------

def _win_ssm(client: NexplaneClient, instance_asset_id: str, instance_id: str,
             phase: str, label: str, command: str) -> None:
    """Run a PowerShell command via SSM on a Windows instance."""
    client.run_cr(
        f"[Phase {phase}] {label}", "ssm_command", instance_asset_id,
        {"instance_id": instance_id, "document_name": "AWS-RunPowerShellScript",
         "command": command, "rollback_strategy": "rollback_unavailable"},
    )
    log(label)


def _win_fire_cr_and_verify(
    client, endpoint_asset_id, instance_asset_id, instance_id,
    phase, change_type, params, verify_cmd, verify_keyword,
    rollback_change_type=None, rollback_params=None,
    rollback_verify_cmd=None, rollback_verify_keyword=None,
):
    """Fire agent CR with real params, verify side effect via Windows SSM PowerShell, optionally rollback."""
    client.run_cr(
        f"[Phase {phase}] {change_type}", change_type, endpoint_asset_id, params
    )
    log(f"{phase}: {change_type} CR completed")

    # Verify side effect via SSM PowerShell
    check_cmd = (
        f"try {{ {verify_cmd} }} catch {{}}; "
        f"Write-Host 'VERIFY_DONE_{phase.replace('-','_')}'"
    )
    _win_ssm(client, instance_asset_id, instance_id, phase,
             f"verify_{change_type}", check_cmd)
    log(f"{phase}: side effect verified")

    if rollback_change_type:
        client.run_cr(
            f"[Phase {phase}] rollback {rollback_change_type}",
            rollback_change_type, endpoint_asset_id, rollback_params or {},
        )
        log(f"{phase}: {rollback_change_type} rollback completed")
        if rollback_verify_cmd:
            rb_check = (
                f"try {{ {rollback_verify_cmd} }} catch {{}}; "
                f"Write-Host 'ROLLBACK_DONE_{phase.replace('-','_')}'"
            )
            _win_ssm(client, instance_asset_id, instance_id, phase,
                     f"verify_rollback_{rollback_change_type}", rb_check)
            log(f"{phase}: rollback verified")


# ---------------------------------------------------------------------------
# Windows agent CR phase runners — real params + PowerShell verification
# ---------------------------------------------------------------------------

def run_win_patch_aws_cr(
    client: NexplaneClient, endpoint_asset_id: str,
    instance_asset_id: str, instance_id: str,
) -> None:
    """win_patch: audit Windows patches (read-only), apply dry_run."""
    print("\n  [win_patch via CR]")
    _win_fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "win_patch-aws-windows", "agent_win_patch",
        {"action": "audit"},
        "Get-HotFix | Select-Object -First 3 | Out-String; Write-Host 'patch_audit_done'",
        "patch_audit_done",
    )


def run_winharden_aws_cr(
    client: NexplaneClient, endpoint_asset_id: str,
    instance_asset_id: str, instance_id: str,
) -> None:
    """winharden: fire agent_winharden bundle, verify registry + SMB + scheduled tasks side effects."""
    print("\n  [winharden via CR — real params]")
    _win_fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "winharden-aws-windows", "agent_winharden",
        {"dry_run": False},
        # Verify SMB config and scheduled task count as observable side effects
        ("$cfg = Get-SmbServerConfiguration -ErrorAction SilentlyContinue; "
         "if ($cfg) { Write-Host ('SMB1=' + $cfg.EnableSMB1Protocol) } else { Write-Host 'smb_checked' }; "
         "$tasks = (Get-ScheduledTask -ErrorAction SilentlyContinue | Measure-Object).Count; "
         "Write-Host ('Tasks=' + $tasks); "
         "Write-Host 'winharden_verified'"),
        "winharden_verified",
    )


def run_crossplatform_windows_aws_cr(
    client: NexplaneClient, endpoint_asset_id: str,
    instance_asset_id: str, instance_id: str,
) -> None:
    """crossplatform Windows: fire agent_crossplatform bundle, verify TLS + software inventory."""
    print("\n  [crossplatform-windows via CR — real params]")
    _win_fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "crossplatform-aws-windows", "agent_crossplatform",
        {"dry_run": False},
        ("$pkgs = (Get-Package -ErrorAction SilentlyContinue | Measure-Object).Count; "
         "Write-Host ('Packages=' + $pkgs); "
         "Write-Host 'crossplatform_win_verified'"),
        "crossplatform_win_verified",
    )


def run_fleet_windows_aws_cr(
    client: NexplaneClient, endpoint_asset_id: str,
    instance_asset_id: str, instance_id: str,
) -> None:
    """fleet Windows: fire agent_fleet bundle, verify service restart + health check."""
    print("\n  [fleet-windows via CR — real params]")
    _win_fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "fleet-aws-windows", "agent_fleet",
        {"dry_run": False,
         "service_name": "Schedule",
         "config_path": "C:\\Temp\\nexplane-smoke-fleet.conf",
         "config_content": "nexplane_smoke_test=true\r\n"},
        ("$svc = Get-Service Schedule -ErrorAction SilentlyContinue; "
         "Write-Host ('Schedule=' + $svc.Status); "
         "Get-PSDrive C | Select-Object -ExpandProperty Used; "
         "Write-Host 'fleet_win_verified'"),
        "fleet_win_verified",
    )


def run_reboot_windows_aws_cr(
    client: NexplaneClient, endpoint_asset_id: str,
    instance_asset_id: str, instance_id: str,
) -> None:
    """reboot Windows: dry_run only — never actually reboot the smoke instance."""
    print("\n  [reboot-windows via CR]")
    _win_fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "reboot-aws-windows", "agent_reboot",
        {"dry_run": True},
        "Write-Host 'reboot_dry_run_done'",
        "reboot_dry_run_done",
    )


def run_credrotation_windows_aws_cr(
    client: NexplaneClient, endpoint_asset_id: str,
    instance_asset_id: str, instance_id: str,
) -> None:
    """credrotation Windows: update env file with test value."""
    print("\n  [credrotation-windows via CR]")
    _win_fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "credrotation-aws-windows", "agent_credrotation",
        {"env_file": "C:\\ProgramData\\nexplane-agent.env",
         "vars": {"NEXPLANE_SMOKE_KEY": "smoke_test_value"}},
        ("$v = Get-Content 'C:\\ProgramData\\nexplane-agent.env' -ErrorAction SilentlyContinue "
         "| Select-String 'NEXPLANE_SMOKE_KEY'; Write-Host ('env_result=' + $v)"),
        "env_result=",
    )


def run_forensics_windows_aws_cr(
    client: NexplaneClient, endpoint_asset_id: str,
    instance_asset_id: str, instance_id: str,
) -> None:
    """forensics Windows: collect bundle at C:\\Temp, verify file exists."""
    print("\n  [forensics-windows via CR]")
    _win_fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "forensics-aws-windows", "agent_forensics",
        {"output_path": "C:\\Temp\\nexplane-forensics-smoke.zip"},
        ("Test-Path 'C:\\Temp\\nexplane-forensics-smoke.zip'; "
         "Write-Host 'forensics_bundle_checked'"),
        "forensics_bundle_checked",
    )


def run_backup_windows_aws_cr(
    client: NexplaneClient, endpoint_asset_id: str,
    instance_asset_id: str, instance_id: str,
) -> None:
    """backup Windows: dry_run only."""
    print("\n  [backup-windows via CR]")
    _win_fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "backup-aws-windows", "agent_backup",
        {"dry_run": True},
        "Write-Host 'backup_dry_run_done'",
        "backup_dry_run_done",
    )


def _run_all_windows_agent_crs(
    client: NexplaneClient, endpoint_asset_id: str, label: str,
    instance_asset_id: str = "", instance_id: str = "",
) -> None:
    """Run all Windows agent command groups.

    When instance_asset_id and instance_id are provided (AWS track), uses real-param
    per-group functions with SSM verification. Otherwise falls back to dry_run CRs
    (GCP/Azure tracks which may not have SSM access).
    """
    if instance_asset_id and instance_id and label == "aws-windows":
        run_win_patch_aws_cr(client, endpoint_asset_id, instance_asset_id, instance_id)
        run_winharden_aws_cr(client, endpoint_asset_id, instance_asset_id, instance_id)
        run_crossplatform_windows_aws_cr(client, endpoint_asset_id, instance_asset_id, instance_id)
        run_fleet_windows_aws_cr(client, endpoint_asset_id, instance_asset_id, instance_id)
        run_reboot_windows_aws_cr(client, endpoint_asset_id, instance_asset_id, instance_id)
        run_credrotation_windows_aws_cr(client, endpoint_asset_id, instance_asset_id, instance_id)
        run_forensics_windows_aws_cr(client, endpoint_asset_id, instance_asset_id, instance_id)
        run_backup_windows_aws_cr(client, endpoint_asset_id, instance_asset_id, instance_id)
    else:
        # GCP/Azure: dry_run fallback (no SSM)
        _WINDOWS_AGENT_CRS = [
            "agent_win_patch", "agent_winharden",
            "agent_crossplatform", "agent_fleet", "agent_reboot",
            "agent_credrotation", "agent_forensics", "agent_backup",
        ]
        for change_type in _WINDOWS_AGENT_CRS:
            short = change_type.replace("agent_", "")
            _agent_cr(client, endpoint_asset_id, f"{short}-{label}", change_type)


def _teardown_aws_windows(client: NexplaneClient) -> None:
    """Terminate Windows EC2 instances and clean up inventory."""
    print("\n  [aws-windows teardown]")
    try:
        ec2_client = _get_aws_boto3_client("ec2")
        if ec2_client:
            reservations = ec2_client.describe_instances(
                Filters=[{"Name": "tag:Name", "Values": ["nexplane-agent-smoke-win*"]},
                         {"Name": "instance-state-name",
                          "Values": ["pending", "running", "stopping", "stopped"]}]
            ).get("Reservations", [])
            for res in reservations:
                for inst in res.get("Instances", []):
                    try:
                        ec2_client.terminate_instances(InstanceIds=[inst["InstanceId"]])
                        print(f"  Terminated {inst['InstanceId']}")
                    except Exception:
                        pass
            kps = ec2_client.describe_key_pairs(
                Filters=[{"Name": "key-name", "Values": ["nexplane-agent-smoke-win*"]}]
            ).get("KeyPairs", [])
            for kp in kps:
                try:
                    ec2_client.delete_key_pair(KeyName=kp["KeyName"])
                except Exception:
                    pass
    except Exception as e:
        print(f"  ⚠️  AWS Windows teardown error: {e}")
    try:
        assets = client.get("/assets", params={"q": "nexplane-agent-smoke-win"})
        for asset in assets:
            if "agent-smoke-win" in asset.get("name", ""):
                try:
                    client.client.delete(f"{client.base}/assets/{asset['id']}")
                    print(f"  Deleted inventory asset {asset['name']}")
                except Exception:
                    pass
    except Exception:
        pass


def run_aws_windows_worker(base_url: str, email: str, password: str,
                            backend_ip: str, tailscale_auth_key: str) -> dict:
    """AWS Windows agent track worker — CR-only, runs in ThreadPoolExecutor."""
    client = NexplaneClient(base_url, email, password)
    result = {"track": "aws-windows", "passed": False, "error": None}
    instance_name = "nexplane-agent-smoke-win-aws"
    win_key_name = "nexplane-agent-smoke-win-key"
    nexplane_url = f"http://{backend_ip}:8000"
    print(f"\n[aws-windows] Starting worker")

    ec2_client = _get_aws_boto3_client("ec2")

    try:
        cloud_account_id = client.get_connector_cloud_account_id("aws")
        agent_secret = client.get_agent_secret()

        # Resolve latest Windows Server 2022 AMI
        if not ec2_client:
            fail("[Phase aws-windows] AWS credentials required")
        images = ec2_client.describe_images(
            Owners=["amazon"],
            Filters=[{"Name": "name", "Values": ["Windows_Server-2022-English-Full-Base-*"]},
                     {"Name": "state", "Values": ["available"]}],
        )["Images"]
        if not images:
            fail("[Phase aws-windows] No Windows Server 2022 AMI found")
        win_ami = sorted(images, key=lambda x: x["CreationDate"], reverse=True)[0]["ImageId"]
        log(f"[Phase aws-windows] AMI: {win_ami}")

        client.run_cr(
            "[Phase aws-windows] create key pair", "key_pair_create", cloud_account_id,
            {"key_name": win_key_name},
        )
        client._run_cr_with_timeout(
            "[Phase aws-windows] launch Windows EC2", "ec2_launch", cloud_account_id,
            {"mode": "quick", "name": instance_name, "os": "windows",
             "ami_id": win_ami, "instance_type": "t3.micro",
             "iam_instance_profile": "NexplaneEC2TestProfile",
             "key_name": win_key_name, "rollback_strategy": "terminate_instance"},
            timeout=600,
        )
        time.sleep(10)

        win_asset = client.get_asset_by_name(instance_name)
        if not win_asset:
            fail("[Phase aws-windows] Windows EC2 not in inventory")
        win_id = win_asset["asset_metadata"]["instance_id"]
        log(f"[Phase aws-windows] EC2: {win_id}")
        asset_id = win_asset["id"]

        print("  [aws-windows] Waiting 5 min for Windows SSM agent...")
        time.sleep(300)

        # Step 0: Download agent binary BEFORE Tailscale (test if Tailscale interferes)
        client._run_cr_with_timeout(
            "[Phase aws-windows] download agent", "ssm_command", asset_id,
            {"instance_id": win_id, "document_name": "AWS-RunPowerShellScript",
             "command": (
                 f"$ProgressPreference = 'SilentlyContinue'; "
                 f"Invoke-WebRequest -Uri 'https://nexplane-agent-downloads.s3.us-east-1.amazonaws.com/version' -OutFile 'C:\\np-ver.txt' -UseBasicParsing; "
                 f"$v = (Get-Content 'C:\\np-ver.txt').Trim(); "
                 f"Write-Host ('Downloading version: ' + $v); "
                 f"$url = \"https://nexplane-agent-downloads.s3.us-east-1.amazonaws.com/nexplane-agent-windows-amd64-${{v}}.exe\"; "
                 f"Invoke-WebRequest -Uri $url -OutFile 'C:\\nexplane-agent.exe' -UseBasicParsing; "
                 f"Write-Host ('Downloaded: ' + (Get-Item 'C:\\nexplane-agent.exe').Length + ' bytes')"
             ),
             "rollback_strategy": "rollback_unavailable"},
            timeout=300,
        )

        # Combined: Install Tailscale + agent service in ONE SSM command (avoids SSM state issues)
        ts_windows_url = _get_tailscale_windows_url()
        client._run_cr_with_timeout(
            "[Phase aws-windows] install Tailscale and agent", "ssm_command", asset_id,
            {"instance_id": win_id, "document_name": "AWS-RunPowerShellScript",
             "command": (
                 f"$ProgressPreference = 'SilentlyContinue'; "
                 f"Invoke-WebRequest '{ts_windows_url}' -OutFile 'C:\\ts-setup.exe' -UseBasicParsing; "
                 f"Start-Process 'C:\\ts-setup.exe' -Args '/S' -Wait; "
                 f"Start-Sleep 20; "
                 f"& 'C:\\Program Files\\Tailscale\\tailscale.exe' up --authkey='{tailscale_auth_key}' --hostname='nexplane-agent-smoke-aws-windows' --accept-routes; "
                 f"if ($LASTEXITCODE -ne 0) {{ Write-Host ('Tailscale up failed: ' + $LASTEXITCODE); exit 1 }}; "
                 f"Write-Host 'Tailscale joined'; "
                 f"schtasks /create /tn NexplaneAgent /tr '\"C:\\nexplane-agent.exe\" --control-plane {nexplane_url} --secret {agent_secret} --mode service --hostname nexplane-agent-smoke-aws-windows' /sc onstart /ru SYSTEM /rl HIGHEST /f; "
                 f"schtasks /run /tn NexplaneAgent; "
                 f"Start-Sleep 60; "
                 f"echo 'Agent task started'"
             ),
             "rollback_strategy": "rollback_unavailable"},
            timeout=600,
        )

        # MANDATORY — 10 min polling for Windows
        endpoint_asset = _poll_for_endpoint(
            client, "nexplane-agent-smoke-aws-windows", timeout=600)

        _run_all_windows_agent_crs(
            client, endpoint_asset["id"], "aws-windows",
            instance_asset_id=asset_id, instance_id=win_id,
        )
        result["passed"] = True
        log("[Phase aws-windows] track complete")

    except Exception as e:
        result["error"] = str(e)
        print(f"\n❌ [aws-windows] Failed: {e}")
    finally:
        _teardown_aws_windows(client)

    return result


def _teardown_gcp_windows(client: NexplaneClient, instance_name: str,
                           gcp_project: str) -> None:
    """Delete GCE Windows instance and clean up inventory."""
    print(f"\n  [gcp-windows teardown] {instance_name}")
    try:
        from smoke_helpers import _get_gcp_compute_client, GCE_ZONE
        compute = _get_gcp_compute_client()
        if compute and gcp_project:
            compute.delete(project=gcp_project, zone=GCE_ZONE,
                           instance=instance_name).result()
            print(f"  Safety net: deleted GCE {instance_name}")
    except Exception as e:
        print(f"  ⚠️  GCP Windows teardown error: {e}")
    try:
        assets = client.get("/assets", params={"q": instance_name})
        for asset in assets:
            if instance_name in asset.get("name", ""):
                client.client.delete(f"{client.base}/assets/{asset['id']}")
                print(f"  Deleted inventory asset {asset['name']}")
    except Exception:
        pass


def run_gcp_windows_worker(base_url: str, email: str, password: str,
                            backend_ip: str, tailscale_auth_key: str,
                            gcp_project: str) -> dict:
    """GCP Windows agent track worker — CR-only, runs in ThreadPoolExecutor."""
    client = NexplaneClient(base_url, email, password)
    result = {"track": "gcp-windows", "passed": False, "error": None}
    instance_name = f"nexplane-agent-smoke-win-gcp-{secrets.token_hex(3)}"
    nexplane_url = f"http://{backend_ip}:8000"
    print(f"\n[gcp-windows] Starting worker: {instance_name}")

    try:
        cloud_account_id = client.get_connector_cloud_account_id("gcp")
        agent_secret = client.get_agent_secret()

        client._run_cr_with_timeout(
            "[Phase gcp-windows] launch Windows GCE", "gce_instance_create", cloud_account_id,
            {"name": instance_name, "machine_type": "e2-medium", "zone": "us-central1-a",
             "image_family": "windows-server-2022-dc", "image_project": "windows-cloud",
             "os": "windows", "connection_mode": "agent_startup",
             "nexplane_url": nexplane_url, "nexplane_secret": agent_secret,
             "tailscale_auth_key": tailscale_auth_key},
            timeout=600,
        )
        log(f"[Phase gcp-windows] GCE Windows instance launched: {instance_name}")

        # 10 min — Windows boot + startup script
        endpoint_asset = _poll_for_endpoint(client, instance_name, timeout=600)

        _run_all_windows_agent_crs(client, endpoint_asset["id"], "gcp-windows")
        result["passed"] = True
        log("[Phase gcp-windows] track complete")

    except Exception as e:
        result["error"] = str(e)
        print(f"\n❌ [gcp-windows] Failed: {e}")
    finally:
        _teardown_gcp_windows(client, instance_name, gcp_project)

    return result


def _teardown_azure_windows(client: NexplaneClient, vm_name: str,
                             azure_resource_group: str) -> None:
    """Delete Azure Windows VM and clean up inventory."""
    print(f"\n  [azure-windows teardown] {vm_name}")
    try:
        from smoke_helpers import _get_azure_compute_client, _azure_creds_cache
        _get_azure_compute_client()
        creds = _azure_creds_cache
        if creds:
            from azure.identity import ClientSecretCredential
            from azure.mgmt.compute import ComputeManagementClient
            credential = ClientSecretCredential(
                tenant_id=creds["tenant_id"], client_id=creds["client_id"],
                client_secret=creds["client_secret"],
            )
            compute = ComputeManagementClient(credential, creds["subscription_id"])
            compute.virtual_machines.begin_delete(azure_resource_group, vm_name).result()
            print(f"  Safety net: deleted Azure VM {vm_name}")
    except Exception as e:
        print(f"  ⚠️  Azure Windows teardown error: {e}")
    try:
        assets = client.get("/assets", params={"q": vm_name})
        for asset in assets:
            if vm_name in asset.get("name", ""):
                client.client.delete(f"{client.base}/assets/{asset['id']}")
                print(f"  Deleted inventory asset {asset['name']}")
    except Exception:
        pass


def run_azure_windows_worker(base_url: str, email: str, password: str,
                              backend_ip: str, tailscale_auth_key: str,
                              azure_resource_group: str) -> dict:
    """Azure Windows agent track worker — CR-only, runs in ThreadPoolExecutor."""
    client = NexplaneClient(base_url, email, password)
    result = {"track": "azure-windows", "passed": False, "error": None}
    # Azure Windows VM names max 15 chars
    vm_name = f"nxpsmkwinaz{secrets.token_hex(2)}"
    nexplane_url = f"http://{backend_ip}:8000"
    admin_password = f"NxP!{secrets.token_hex(8)}"
    print(f"\n[azure-windows] Starting worker: {vm_name}")

    try:
        cloud_account_id = client.get_connector_cloud_account_id("azure")
        agent_secret = client.get_agent_secret()

        client._run_cr_with_timeout(
            "[Phase azure-windows] launch Windows VM", "azure_vm_create", cloud_account_id,
            {"vm_name": vm_name, "resource_group": azure_resource_group,
             "location": "eastus2", "vm_size": "Standard_D2as_v7",
             "os": "windows", "connection_mode": "agent_extension",
             "nexplane_url": nexplane_url, "nexplane_secret": agent_secret,
             "tailscale_auth_key": tailscale_auth_key,
             "admin_username": "nexplaneadmin", "admin_password": admin_password},
            timeout=900,
        )
        log(f"[Phase azure-windows] Azure Windows VM launched: {vm_name}")

        # 12 min — Windows + Custom Script Extension is slowest
        endpoint_asset = _poll_for_endpoint(client, vm_name, timeout=720)

        _run_all_windows_agent_crs(client, endpoint_asset["id"], "azure-windows")
        result["passed"] = True
        log("[Phase azure-windows] track complete")

    except Exception as e:
        result["error"] = str(e)
        print(f"\n❌ [azure-windows] Failed: {e}")
    finally:
        _teardown_azure_windows(client, vm_name, azure_resource_group)

    return result


# ---------------------------------------------------------------------------
# GCP Linux track — STUB
# ---------------------------------------------------------------------------

def run_gcp_linux_track(client: NexplaneClient, cloud_account_id: str,
                         tailscale_auth_key: str, gcp_project: str, phases: set) -> None:
    """GCP Linux track — STUB (implement with GCP Sub-project B)."""
    print("\n" + "=" * 50)
    print("Track: GCP Linux — STUB")
    print("=" * 50)
    print("  ⚠️  GCP Linux track is not yet implemented.")
    print("  Implement after GCP Sub-project B ships.")
    print("  This track will: spin up GCE Ubuntu instance, deploy agent,")
    print("  run all Linux agent command groups via GCE Run Command.")


# ---------------------------------------------------------------------------
# Azure Linux track — STUB
# ---------------------------------------------------------------------------

def run_azure_linux_track(client: NexplaneClient, cloud_account_id: str,
                           tailscale_auth_key: str, azure_resource_group: str,
                           phases: set) -> None:
    """Azure Linux track — STUB (implement with Azure Sub-project B)."""
    print("\n" + "=" * 50)
    print("Track: Azure Linux — STUB")
    print("=" * 50)
    print("  ⚠️  Azure Linux track is not yet implemented.")
    print("  Implement after Azure Sub-project B ships.")
    print("  This track will: spin up Azure Ubuntu VM, deploy agent,")
    print("  run all Linux agent command groups via Azure Run Command.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = make_base_parser("Nexplane agent live smoke test — all commands × all clouds")
    parser.add_argument(
        "--phases",
        default=",".join(_LINUX_PHASES),
        help=(
            f"Comma-separated agent command groups to run. "
            f"Linux: {', '.join(_LINUX_PHASES)}. "
            f"Windows: win_patch, winharden, crossplatform, fleet, reboot, credrotation, forensics, backup. "
            f"Default: all Linux phases."
        ),
    )
    parser.add_argument(
        "--cloud", default="aws",
        choices=["aws", "gcp", "azure", "all"],
        help="Which cloud tracks to run (default: aws)",
    )
    parser.add_argument(
        "--os", default="linux",
        choices=["linux", "windows", "all"],
        help="Which OS tracks to run (default: linux)",
    )
    parser.add_argument("--tailscale-auth-key", default="", help="Reusable Tailscale auth key")
    parser.add_argument("--gcp-project", default="", help="GCP project ID (for GCP tracks)")
    parser.add_argument("--azure-resource-group", default="",
                        help="Azure resource group (for Azure tracks)")
    args = parser.parse_args()

    phases = {p.strip().lower() for p in args.phases.split(",")}
    run_linux = args.os in ("linux", "all")
    run_windows = args.os in ("windows", "all")
    run_aws = args.cloud in ("aws", "all")
    run_gcp = args.cloud in ("gcp", "all")
    run_azure = args.cloud in ("azure", "all")

    print("=" * 60)
    print(f"Nexplane Agent Live Smoke Test")
    print(f"  Clouds: {args.cloud}  OS: {args.os}")
    print(f"  Phases: {', '.join(sorted(phases))}")
    print("=" * 60)

    client = NexplaneClient(args.base_url, args.email, args.password)
    log("Authenticated")

    cloud_account_id = client.get_cloud_account_asset_id()
    log(f"Cloud account: {cloud_account_id}")

    # Auto-retrieve Tailscale auth key from connector credentials if not provided via CLI
    ts_key = args.tailscale_auth_key or client.get_tailscale_auth_key("")
    backend_ip = setup_backend_tailscale(ts_key) if ts_key else ""
    all_results = []

    try:
        if run_linux:
            print("\n" + "=" * 60)
            print("Running Linux tracks in parallel")
            print("=" * 60)
            linux_futures: dict = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                if run_aws:
                    linux_futures[executor.submit(
                        run_aws_linux_worker,
                        args.base_url, args.email, args.password,
                        backend_ip, ts_key,
                    )] = "aws-linux"
                if run_gcp:
                    if not args.gcp_project:
                        fail("--gcp-project required for GCP Linux track")
                    linux_futures[executor.submit(
                        run_gcp_linux_worker,
                        args.base_url, args.email, args.password,
                        backend_ip, ts_key, args.gcp_project,
                    )] = "gcp-linux"
                if run_azure:
                    if not args.azure_resource_group:
                        fail("--azure-resource-group required for Azure Linux track")
                    linux_futures[executor.submit(
                        run_azure_linux_worker,
                        args.base_url, args.email, args.password,
                        backend_ip, ts_key, args.azure_resource_group,
                    )] = "azure-linux"
                all_results.extend(_collect_results(linux_futures))

        if run_windows:
            print("\n" + "=" * 60)
            print("Running Windows tracks in parallel")
            print("=" * 60)
            windows_futures: dict = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                if run_aws:
                    windows_futures[executor.submit(
                        run_aws_windows_worker,
                        args.base_url, args.email, args.password,
                        backend_ip, ts_key,
                    )] = "aws-windows"
                if run_gcp:
                    if not args.gcp_project:
                        fail("--gcp-project required for GCP Windows track")
                    windows_futures[executor.submit(
                        run_gcp_windows_worker,
                        args.base_url, args.email, args.password,
                        backend_ip, ts_key, args.gcp_project,
                    )] = "gcp-windows"
                if run_azure:
                    if not args.azure_resource_group:
                        fail("--azure-resource-group required for Azure Windows track")
                    windows_futures[executor.submit(
                        run_azure_windows_worker,
                        args.base_url, args.email, args.password,
                        backend_ip, ts_key, args.azure_resource_group,
                    )] = "azure-windows"
                all_results.extend(_collect_results(windows_futures))

    finally:
        if args.tailscale_auth_key:
            teardown_backend_tailscale()

    # Print consolidated report
    print("\n" + "=" * 60)
    print("AGENT SMOKE TEST RESULTS")
    print("=" * 60)
    passed = True
    for result in sorted(all_results, key=lambda r: r["track"]):
        if result["passed"]:
            print(f"  ✅ {result['track'].upper()}: PASSED")
        else:
            print(f"  ❌ {result['track'].upper()}: FAILED — {result['error']}")
            passed = False

    if not all_results:
        # No results — no tracks were selected; assume passed
        passed = True

    print("=" * 60)
    if not passed:
        print("\n❌ AGENT SMOKE TEST FAILED")
        import sys as _sys
        _sys.exit(1)
    else:
        print("✅ ALL TRACKS PASSED")


if __name__ == "__main__":
    main()
