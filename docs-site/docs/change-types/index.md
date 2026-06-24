# Change Types

A **change type** is a named, parameterized operation that a connector can perform. Every change type in Nexplane has three required components:

1. **Parameter schema** — typed input fields with validation rules
2. **Execution logic** — the steps to apply the change, including before-state capture
3. **Rollback logic** — the steps to reverse the change using the captured snapshot

## Finding available change types

In the UI, navigate to **Settings → Change Types** to see the full list of registered change types, their connector, parameter schema, and rollback behavior.

When creating a change request, the change type dropdown is filtered to types supported by the connector accounts you have configured.

## Change type catalog

Change types are grouped by category. This is the open-source catalog; the full, always-current list is visible under **Settings → Change Types** in the UI.

| Category | Change Types |
|----------|-------------|
| Infrastructure | `dns_update`, `security_group_update`, `microsegmentation_policy`, `snapshot_asset` |
| EC2 | `ec2_launch`, `ec2_start`, `ec2_stop`, `ec2_reboot`, `ec2_terminate`, `key_pair_create`, `key_pair_delete` |
| SSM | `ssm_command` |
| Network | `tailscale_join`, `tailscale_remove` |
| IP Migration | `change_ip`, `migrate_ip`, `ip_campaign` |
| Agent | `deploy_nexplane_agent`, `patch_packages`, `patch_campaign`, `isolate_host`, `rolling_restart`, `canary_config_push`, `distribute_file`, `fleet_health_check` |
| Identity | `offboard_user`, `onboard_user`, `key_rotation`, `rotate_db_credentials`, `rotate_ssh_keys`, `rotate_api_key`, `rotate_service_account` |
| IAM | `iam_user_create`, `iam_user_delete` |
| S3 Storage | `s3_bucket_create`, `s3_bucket_delete`, `s3_lifecycle_configure` |
| DNS (Route53) | `route53_zone_create`, `route53_record_upsert`, `route53_record_delete` |
| RDS | `rds_instance_create`, `rds_instance_delete`, `rds_snapshot_create` |
| Observability | `cloudwatch_alarm_create`, `cloudwatch_alarm_delete` |
| Incident Response | `lockdown_account`, `phishing_response`, `preserve_evidence` |
| IaC (local) | `terraform_local_apply`, `ansible_local_playbook` |
| IaC (remote) | `terraform_apply`, `ansible_playbook`, `helm_upgrade` |
| Database | `provision_db_user`, `deprovision_db_user`, `db_permission_change`, `configure_db_audit`, `promote_db_replica`, `db_connection_config` |
| Backup / Recovery | `create_backup`, `verify_backup`, `restore_files`, `dr_failover`, `scheduled_reboot` |
| Compliance | `enforce_cis_benchmark`, `collect_evidence` |
| SaaS (Google Workspace) | `remove_from_groups`, `reset_2fa`, `revoke_oauth_tokens`, `wipe_mobile_device`, `suspend_user`, `unsuspend_user` |
| SaaS (GitHub) | `remove_org_member`, `revoke_user_pats`, `enforce_branch_protection`, `archive_repo`, `disable_actions`, `enable_actions` |
| SaaS (Slack) | `deactivate_user`, `reactivate_user` |
| SaaS (Entra ID) | `remove_from_teams`, `assign_license`, `remove_license`, `revoke_sessions`, `disable_user` |
| SaaS (Kubernetes) | `restart_deployment`, `scale_deployment`, `apply_network_policy`, `update_rbac`, `rotate_secret`, `helm_upgrade`, `helm_rollback` |
| macOS | `macos_filevault_enable`, `macos_gatekeeper_enable`, `macos_santa_install`, `macos_santa_rule_add`, `macos_santa_mode_set`, `macos_softwareupdate_install`, `macos_profiles_install`, `macos_defaults_write`, `macos_sysinfo` … (23 macOS change types) |
| Security Policy | `apply_seccomp_profile`, `apply_apparmor_profile`, `apply_selinux_policy`, `apply_ebpf_policy` (synthesized from soak sessions) |
| Telemetry | `telemetry_agent_deploy`, `remote_command` |

## Rollback guarantee

Every change type that modifies state must implement a rollback handler. If a change type cannot provide a rollback guarantee (e.g. because the operation is inherently irreversible), this is documented on the connector page and the CR detail will show **Rollback: Not available**.
