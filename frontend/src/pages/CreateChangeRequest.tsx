// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import { useState, useEffect } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";
import { changeRequestsApi, assetsApi } from "../api/endpoints";
import { PageHeader } from "../components/PageHeader";
import type { AssetType, ChangeType } from "../types/api";

const CHANGE_TYPE_META: Partial<Record<ChangeType, { label: string; description: string; outcomeTemplate: string }>> = {
  dns_update: {
    label: "DNS Update",
    description: "Update a DNS record (A, CNAME, MX, TXT)",
    outcomeTemplate: JSON.stringify({ record_name: "api.example.com", record_type: "A", new_value: "203.0.113.1", ttl: 300, rollback_strategy: "restore_previous_record" }, null, 2),
  },
  snapshot_asset: {
    label: "Snapshot Asset",
    description: "Create a point-in-time snapshot of a cloud asset",
    outcomeTemplate: JSON.stringify({ snapshot_tag: "pre-deployment-2026", rollback_strategy: "rollback_unavailable" }, null, 2),
  },
  security_group_update: {
    label: "Security Group Update",
    description: "Modify firewall or cloud security group rules",
    outcomeTemplate: JSON.stringify({ group_id: "sg-abc123", rules: [{ action: "add", protocol: "tcp", port: 443, cidr: "10.0.0.0/8" }], rollback_strategy: "restore_rule_snapshot" }, null, 2),
  },
  key_rotation: {
    label: "Key Rotation",
    description: "Rotate API keys, secrets, or credentials",
    outcomeTemplate: JSON.stringify({ key_type: "api_key", service: "payment-service", consumers: ["app-1", "app-2"], grace_period_hours: 24, rollback_strategy: "cancel_revocation" }, null, 2),
  },
  telemetry_agent_deploy: {
    label: "Telemetry Agent Deploy",
    description: "Deploy logging, metrics, or security agent to hosts",
    outcomeTemplate: JSON.stringify({ agent_type: "filebeat", agent_version: "8.12.0", agent_config: { output: "elasticsearch", index: "logs-*" }, rollback_strategy: "uninstall_agent" }, null, 2),
  },
  remote_command: {
    label: "Remote Command",
    description: "Execute an approved command template on target hosts",
    outcomeTemplate: JSON.stringify({ template_id: "restart_service", parameters: { service_name: "nginx" }, rollback_strategy: "rollback_unavailable" }, null, 2),
  },
  microsegmentation_policy: {
    label: "Microsegmentation Policy",
    description: "Stage a network microsegmentation policy (simulation mode)",
    outcomeTemplate: JSON.stringify({ policy_rules: [{ src: "app-tier", dst: "db-tier", port: 5432, action: "allow" }, { src: "app-tier", dst: "internet", port: "any", action: "deny" }], critical_flows: [{ name: "app-to-db", src: "app-tier", dst: "db-tier", port: 5432 }], rollback_strategy: "remove_staged_policy" }, null, 2),
  },
  ec2_stop: {
    label: "Stop EC2 Instance",
    description: "Gracefully stop a running instance. Takes an EBS snapshot first as a safety net.",
    outcomeTemplate: JSON.stringify({
      instance_id: "i-0123456789abcdef0",
      snapshot_tag: "pre-stop-nexplane",
      rollback_strategy: "start_instance",
    }, null, 2),
  },
  ec2_start: {
    label: "Start EC2 Instance",
    description: "Start a stopped EC2 instance.",
    outcomeTemplate: JSON.stringify({
      instance_id: "i-0123456789abcdef0",
      rollback_strategy: "stop_instance",
    }, null, 2),
  },
  ec2_reboot: {
    label: "Reboot EC2 Instance",
    description: "Soft reboot — stays on the same host, keeps its public IP.",
    outcomeTemplate: JSON.stringify({
      instance_id: "i-0123456789abcdef0",
      rollback_strategy: "rollback_unavailable",
    }, null, 2),
  },
  ec2_stop_start: {
    label: "Restart EC2 Instance",
    description: "Full power cycle (stop then start). Instance may get a new public IP if not using an Elastic IP.",
    outcomeTemplate: JSON.stringify({
      instance_id: "i-0123456789abcdef0",
      rollback_strategy: "stop_instance",
    }, null, 2),
  },
  ec2_launch: {
    label: "Launch EC2 Instance",
    description: "Launch a new instance. Set mode to 'quick' (free-tier defaults), 'clone' (copy existing), or 'spec' (full parameters).",
    outcomeTemplate: JSON.stringify({
      mode: "quick",
      name: "my-new-instance",
      os: "amazon_linux",
      iam_instance_profile: "NexplaneEC2TestProfile",
      key_name: "",
      rollback_strategy: "terminate_instance",
    }, null, 2),
  },
  key_pair_create: {
    label: "Create Key Pair",
    description: "Create an EC2 key pair and store it in the asset inventory.",
    outcomeTemplate: JSON.stringify({
      key_name: "nexplane-test-key",
      rollback_strategy: "delete_key_pair",
    }, null, 2),
  },
  ssm_command: {
    label: "Run SSM Command",
    description: "Execute a shell command on an EC2 instance via AWS Systems Manager.",
    outcomeTemplate: JSON.stringify({
      instance_id: "i-0123456789abcdef0",
      document_name: "AWS-RunShellScript",
      command: "whoami && hostname",
      rollback_strategy: "rollback_unavailable",
    }, null, 2),
  },
  ec2_terminate: {
    label: "Terminate EC2 Instance",
    description: "Permanently terminate an instance. Takes a mandatory snapshot first. Irreversible.",
    outcomeTemplate: JSON.stringify({
      instance_id: "i-0123456789abcdef0",
      snapshot_tag: "pre-terminate-nexplane",
      confirm_terminate: true,
      rollback_strategy: "rollback_unavailable",
    }, null, 2),
  },
  rolling_restart: {
    label: "Rolling Service Restart",
    description: "Restart a service across a fleet of hosts in safe batches with configurable abort threshold.",
    outcomeTemplate: JSON.stringify({
      service_name: "nginx",
      asset_group: { asset_ids: [] },
      batch_size_pct: 10,
      abort_threshold_pct: 25,
      rollback_strategy: "restore_previous_service_state",
    }, null, 2),
  },
  canary_config_push: {
    label: "Canary Config Push",
    description: "Push a config file to one canary host first, verify, then roll out to the full group.",
    outcomeTemplate: JSON.stringify({
      file_path: "/etc/nginx/nginx.conf",
      file_content: "",
      canary_asset_id: 0,
      verification_command: "nginx -t",
      asset_group: { asset_ids: [] },
      rollback_strategy: "restore_previous_config",
    }, null, 2),
  },
  distribute_file: {
    label: "Distribute File",
    description: "Push a file to all hosts in an asset group simultaneously.",
    outcomeTemplate: JSON.stringify({
      file_path: "/etc/ssl/certs/ca.crt",
      file_content: "",
      permissions: "0644",
      post_command: "update-ca-certificates",
      asset_group: { asset_ids: [] },
      rollback_strategy: "restore_previous_file",
    }, null, 2),
  },
  fleet_health_check: {
    label: "Fleet Health Check",
    description: "Run a preflight health check across all hosts: disk, load, pending reboots, service status.",
    outcomeTemplate: JSON.stringify({ asset_group: { asset_ids: [] }, required_services: [], rollback_strategy: "rollback_unavailable" }, null, 2),
  },
  // Patching
  patch_packages: {
    label: "Patch Packages",
    description: "Apply security patches to specific packages or CVEs on target hosts.",
    outcomeTemplate: JSON.stringify({ mode: "security_only", packages: [], cve_id: null, dry_run: false, rollback_strategy: "uninstall_patches" }, null, 2),
  },
  patch_campaign: {
    label: "Patch Campaign",
    description: "Rolling patch campaign across a fleet — batched with abort-on-error threshold.",
    outcomeTemplate: JSON.stringify({ cve_id: "CVE-2024-XXXX", batch_size_pct: 10, abort_threshold_pct: 25, dry_run: false, rollback_strategy: "uninstall_patches" }, null, 2),
  },
  // Identity lifecycle
  offboard_user: {
    label: "Offboard User",
    description: "Disable a user across all connected identity systems (AD, Okta, Google, GitHub, Slack).",
    outcomeTemplate: JSON.stringify({ user_email: "user@example.com", isolate_endpoints: false, rollback_strategy: "re_enable_account" }, null, 2),
  },
  onboard_user: {
    label: "Onboard User",
    description: "Provision a new user across all connected identity systems.",
    outcomeTemplate: JSON.stringify({ user_email: "newuser@example.com", display_name: "New User", groups: [], role: "member", rollback_strategy: "deprovision_account" }, null, 2),
  },
  // Credential rotation
  rotate_db_credentials: {
    label: "Rotate DB Credentials",
    description: "Generate new DB password, update the DB user, update config files, restart service.",
    outcomeTemplate: JSON.stringify({ db_type: "postgres", db_host: "localhost", db_port: 5432, username: "appuser", config_file_path: "/etc/app/.env", service_name: "app", rollback_strategy: "restore_previous_credentials" }, null, 2),
  },
  rotate_ssh_keys: {
    label: "Rotate SSH Keys",
    description: "Remove old key by fingerprint from authorized_keys, add new public key across fleet.",
    outcomeTemplate: JSON.stringify({ old_key_fingerprint: "SHA256:...", new_public_key: "ssh-rsa AAAA...", username: "ubuntu", rollback_strategy: "restore_previous_key" }, null, 2),
  },
  rotate_api_key: {
    label: "Rotate API Key",
    description: "Rotate an API key (AWS IAM, Okta, GitHub PAT) and propagate to consumers.",
    outcomeTemplate: JSON.stringify({ key_type: "aws_iam", username: "svc-account", propagation_targets: [], rollback_strategy: "restore_previous_key" }, null, 2),
  },
  rotate_service_account: {
    label: "Rotate Service Account",
    description: "Rotate a service account password in AD or Okta and update dependent services.",
    outcomeTemplate: JSON.stringify({ provider: "active_directory", account_name: "svc-app", service_names: [], rollback_strategy: "restore_previous_credentials" }, null, 2),
  },
  // IaC
  terraform_local_apply: {
    label: "Terraform Apply (Local CLI)",
    description: "Run terraform plan + apply using the local Terraform binary inside the Nexplane backend container.",
    outcomeTemplate: JSON.stringify({
      tf_content: "# Paste your .tf content here\n",
      rollback_strategy: "terraform_destroy_local",
    }, null, 2),
  },
  ansible_local_playbook: {
    label: "Ansible Playbook (Local CLI)",
    description: "Run an Ansible playbook via the local Ansible binary using SSM as the connection transport — no SSH or AWX needed.",
    outcomeTemplate: JSON.stringify({
      instance_id: "i-0123456789abcdef0",
      playbook_content: "---\n- name: Example\n  hosts: all\n  gather_facts: yes\n  tasks:\n    - name: Print hostname\n      ansible.builtin.debug:\n        msg: \"{{ ansible_hostname }}\"\n",
      extra_vars: {},
      rollback_strategy: "rollback_unavailable",
    }, null, 2),
  },
  terraform_apply: {
    label: "Terraform Apply",
    description: "Run terraform plan → review in Nexplane → terraform apply on approval.",
    outcomeTemplate: JSON.stringify({ working_directory: "/infra/prod", workspace: "default", var_file: null, target: null, rollback_strategy: "terraform_destroy" }, null, 2),
  },
  ansible_playbook: {
    label: "Ansible Playbook",
    description: "Run an Ansible playbook with --check mode as preflight, then apply on approval.",
    outcomeTemplate: JSON.stringify({ playbook_path: "/playbooks/harden.yml", inventory: "hosts.ini", extra_vars: {}, limit: null, rollback_strategy: "run_rollback_playbook" }, null, 2),
  },
  helm_upgrade: {
    label: "Helm Upgrade",
    description: "Upgrade a Helm release with --atomic (auto-rollback on failure).",
    outcomeTemplate: JSON.stringify({ release_name: "my-app", chart: "stable/my-app", chart_version: "1.2.0", namespace: "default", values: {}, rollback_strategy: "helm_rollback" }, null, 2),
  },
  // Database administration
  provision_db_user: {
    label: "Provision DB User",
    description: "Create a DB user with specified grants on PostgreSQL, MySQL, or MSSQL.",
    outcomeTemplate: JSON.stringify({ db_type: "postgres", db_host: "localhost", username: "readonly", grants: ["public.users:SELECT"], rollback_strategy: "drop_db_user" }, null, 2),
  },
  deprovision_db_user: {
    label: "Deprovision DB User",
    description: "Revoke all grants and drop a DB user.",
    outcomeTemplate: JSON.stringify({ db_type: "postgres", db_host: "localhost", username: "ex-employee", rollback_strategy: "rollback_unavailable" }, null, 2),
  },
  db_permission_change: {
    label: "DB Permission Change",
    description: "Grant or revoke specific permissions on a database user.",
    outcomeTemplate: JSON.stringify({ db_type: "postgres", db_host: "localhost", target_user: "appuser", grants_to_add: [], grants_to_remove: [], rollback_strategy: "restore_previous_grants" }, null, 2),
  },
  configure_db_audit: {
    label: "Configure DB Audit",
    description: "Enable audit logging (pg_audit, general_log, SQL Audit) on a database.",
    outcomeTemplate: JSON.stringify({ db_type: "postgres", audit_level: "ddl", log_path: "/var/log/pg_audit.log", rollback_strategy: "disable_db_audit" }, null, 2),
  },
  promote_db_replica: {
    label: "Promote DB Replica",
    description: "Promote an RDS read replica to standalone primary (failover). Irreversible.",
    outcomeTemplate: JSON.stringify({ replica_identifier: "my-db-replica", update_dns_record: true, dns_record: "db.internal.example.com", rollback_strategy: "rollback_unavailable" }, null, 2),
  },
  db_connection_config: {
    label: "DB Connection Config",
    description: "Update max_connections or pg_hba.conf rules.",
    outcomeTemplate: JSON.stringify({ db_type: "postgres", max_connections: 200, reload_only: true, rollback_strategy: "restore_previous_config" }, null, 2),
  },
  // Backup & recovery
  create_backup: {
    label: "Create Backup",
    description: "Create an EBS/RDS snapshot or agent-side restic backup.",
    outcomeTemplate: JSON.stringify({ backup_type: "ebs_snapshot", target_resource_id: "vol-abc123", backup_name: "pre-deploy", retention_days: 30, rollback_strategy: "rollback_unavailable" }, null, 2),
  },
  verify_backup: {
    label: "Verify Backup",
    description: "Restore a backup to a temp environment, run health checks, then destroy.",
    outcomeTemplate: JSON.stringify({ backup_id: "snap-abc123", verification_query: "SELECT 1", terminate_after_verify: true, rollback_strategy: "rollback_unavailable" }, null, 2),
  },
  restore_files: {
    label: "Restore Files",
    description: "Restore specific files or directories from a backup snapshot.",
    outcomeTemplate: JSON.stringify({ backup_snapshot_id: "snap-abc123", restore_paths: ["/etc/app"], destination_path: "/etc/app", backup_tool: "restic", rollback_strategy: "restore_previous_files" }, null, 2),
  },
  dr_failover: {
    label: "DR Failover",
    description: "Fail over to DR site — updates Route53 weighted routing and measures RTO.",
    outcomeTemplate: JSON.stringify({ hosted_zone_id: "Z123ABC", record_name: "api.example.com", dr_target: "api-dr.example.com", target_weight: 100, rollback_strategy: "restore_primary_dns" }, null, 2),
  },
  scheduled_reboot: {
    label: "Scheduled Reboot",
    description: "Schedule a graceful reboot with post-reboot service health verification.",
    outcomeTemplate: JSON.stringify({ reboot_at: "2026-06-01T02:00:00Z", verify_services: ["nginx", "app"], rollback_strategy: "rollback_unavailable" }, null, 2),
  },
  // Compliance
  enforce_cis_benchmark: {
    label: "Enforce CIS Benchmark",
    description: "Audit and remediate CIS controls (Level 1 or 2) on Linux hosts.",
    outcomeTemplate: JSON.stringify({ level: 2, os_family: "debian", dry_run: false, rollback_strategy: "restore_previous_config" }, null, 2),
  },
  collect_evidence: {
    label: "Collect Evidence",
    description: "Gather config files and command outputs for audit evidence (SOC2, PCI, ISO27001).",
    outcomeTemplate: JSON.stringify({ framework: "soc2", control_id: "CC6.1", asset_ids: [], rollback_strategy: "rollback_unavailable" }, null, 2),
  },
  // Tailscale / Nexplane Agent
  tailscale_join: {
    label: "Join Tailscale Network",
    description: "Install Tailscale on an EC2 instance and enroll it in your Tailscale network.",
    outcomeTemplate: JSON.stringify({
      instance_id: "i-0123456789abcdef0",
      auth_key: "",
      hostname: "nexplane-host",
      rollback_strategy: "tailscale_remove",
    }, null, 2),
  },
  tailscale_remove: {
    label: "Remove from Tailscale",
    description: "Remove Tailscale from an EC2 instance and deregister it.",
    outcomeTemplate: JSON.stringify({
      instance_id: "i-0123456789abcdef0",
      rollback_strategy: "rollback_unavailable",
    }, null, 2),
  },
  deploy_nexplane_agent: {
    label: "Deploy Nexplane Agent",
    description: "Download and install the Nexplane agent on an EC2 instance via SSM.",
    outcomeTemplate: JSON.stringify({
      instance_id: "i-0123456789abcdef0",
      nexplane_url: "http://100.x.x.x:8000",
      nexplane_secret: "",
      rollback_strategy: "remove_nexplane_agent",
    }, null, 2),
  },
  // Misc
  s3_block_public_access: {
    label: "S3 Block Public Access",
    description: "Enable S3 Block Public Access settings on a bucket.",
    outcomeTemplate: JSON.stringify({ bucket_name: "my-bucket", rollback_strategy: "restore_s3_public_access" }, null, 2),
  },
  iam_enforce_mfa: {
    label: "IAM Enforce MFA",
    description: "Enforce MFA requirement on an IAM user or group.",
    outcomeTemplate: JSON.stringify({ target: "user", username: "svc-account", rollback_strategy: "remove_mfa_requirement" }, null, 2),
  },
  iam_user_create: {
    label: "Create IAM User",
    description: "Create a new IAM user in an AWS account.",
    outcomeTemplate: JSON.stringify({ username: "", rollback_strategy: "delete_iam_user" }, null, 2),
  },
  iam_user_delete: {
    label: "Delete IAM User",
    description: "Delete an IAM user and all associated access keys.",
    outcomeTemplate: JSON.stringify({ username: "", rollback_strategy: "rollback_unavailable" }, null, 2),
  },
  s3_bucket_create: {
    label: "Create S3 Bucket",
    description: "Create a new S3 bucket in an AWS account.",
    outcomeTemplate: JSON.stringify({ bucket_name: "", rollback_strategy: "delete_s3_bucket" }, null, 2),
  },
  s3_bucket_delete: {
    label: "Delete S3 Bucket",
    description: "Empty and delete an S3 bucket.",
    outcomeTemplate: JSON.stringify({ bucket_name: "", rollback_strategy: "rollback_unavailable" }, null, 2),
  },
  s3_lifecycle_configure: {
    label: "Configure S3 Lifecycle",
    description: "Set object expiration and transition rules on an S3 bucket.",
    outcomeTemplate: JSON.stringify({ bucket_name: "", rules: [], rollback_strategy: "restore_prior_lifecycle" }, null, 2),
  },
  route53_zone_create: {
    label: "Create Hosted Zone",
    description: "Create a new Route53 hosted zone in an AWS account.",
    outcomeTemplate: JSON.stringify({ zone_name: "", private: true, rollback_strategy: "delete_route53_zone" }, null, 2),
  },
  route53_record_upsert: {
    label: "Create / Update DNS Record",
    description: "Create or update a DNS record in a Route53 hosted zone.",
    outcomeTemplate: JSON.stringify({ zone_id: "", name: "", record_type: "A", values: [], ttl: 300, rollback_strategy: "delete_route53_record" }, null, 2),
  },
  route53_record_delete: {
    label: "Delete DNS Record",
    description: "Delete a DNS record from a Route53 hosted zone.",
    outcomeTemplate: JSON.stringify({ zone_id: "", name: "", record_type: "A", values: [], ttl: 300, rollback_strategy: "upsert_route53_record" }, null, 2),
  },
  rds_instance_create: {
    label: "Create RDS Instance",
    description: "Launch a new RDS database instance in an AWS account.",
    outcomeTemplate: JSON.stringify({ db_instance_identifier: "", engine: "mysql", db_instance_class: "db.t3.micro", master_username: "admin", master_password: "", allocated_storage: 20, rollback_strategy: "delete_rds_instance" }, null, 2),
  },
  rds_instance_delete: {
    label: "Delete RDS Instance",
    description: "Permanently delete an RDS database instance.",
    outcomeTemplate: JSON.stringify({ db_instance_identifier: "", rollback_strategy: "rollback_unavailable" }, null, 2),
  },
  rds_snapshot_create: {
    label: "Create RDS Snapshot",
    description: "Create a manual snapshot of an RDS database instance.",
    outcomeTemplate: JSON.stringify({ db_instance_identifier: "", snapshot_identifier: "", rollback_strategy: "delete_rds_snapshot" }, null, 2),
  },
  cloudwatch_alarm_create: {
    label: "Create CloudWatch Alarm",
    description: "Create a CloudWatch alarm for a metric threshold.",
    outcomeTemplate: JSON.stringify({ alarm_name: "", metric_name: "CPUUtilization", namespace: "AWS/EC2", threshold: 80, comparison_operator: "GreaterThanThreshold", evaluation_periods: 1, period: 60, dimensions: [], rollback_strategy: "delete_cloudwatch_alarm" }, null, 2),
  },
  cloudwatch_alarm_delete: {
    label: "Delete CloudWatch Alarm",
    description: "Delete a CloudWatch alarm.",
    outcomeTemplate: JSON.stringify({ alarm_name: "", rollback_strategy: "rollback_unavailable" }, null, 2),
  },
  alb_create: {
    label: "Create Application Load Balancer",
    description: "Create an ALB with specified subnets and security groups.",
    outcomeTemplate: JSON.stringify({ name: "", subnets: [], security_group_ids: [], scheme: "internet-facing", rollback_strategy: "delete_alb" }, null, 2),
  },
  alb_delete: {
    label: "Delete Application Load Balancer",
    description: "Delete an ALB by ARN.",
    outcomeTemplate: JSON.stringify({ lb_arn: "", rollback_strategy: "rollback_unavailable" }, null, 2),
  },
  target_group_create: {
    label: "Create Target Group",
    description: "Create an ALB target group for EC2 instances or IPs.",
    outcomeTemplate: JSON.stringify({ name: "", protocol: "HTTP", port: 80, vpc_id: "", rollback_strategy: "delete_target_group" }, null, 2),
  },
  target_group_delete: {
    label: "Delete Target Group",
    description: "Delete a target group by ARN.",
    outcomeTemplate: JSON.stringify({ tg_arn: "", rollback_strategy: "rollback_unavailable" }, null, 2),
  },
  register_targets: {
    label: "Register Targets",
    description: "Register EC2 instances or IPs with an ALB target group.",
    outcomeTemplate: JSON.stringify({ tg_arn: "", targets: [{ id: "", port: 80 }], rollback_strategy: "deregister_targets" }, null, 2),
  },
  deregister_targets: {
    label: "Deregister Targets",
    description: "Remove EC2 instances or IPs from an ALB target group.",
    outcomeTemplate: JSON.stringify({ tg_arn: "", targets: [{ id: "" }], rollback_strategy: "register_targets" }, null, 2),
  },
  listener_create: {
    label: "Create ALB Listener",
    description: "Add a listener to an ALB forwarding to a target group.",
    outcomeTemplate: JSON.stringify({ lb_arn: "", protocol: "HTTP", port: 80, tg_arn: "", rollback_strategy: "rollback_unavailable" }, null, 2),
  },
  listener_modify: {
    label: "Modify ALB Listener",
    description: "Update a listener's port, protocol, or default action.",
    outcomeTemplate: JSON.stringify({ listener_arn: "", port: 443, protocol: "HTTPS", tg_arn: "", rollback_strategy: "rollback_unavailable" }, null, 2),
  },
  listener_delete: {
    label: "Delete ALB Listener",
    description: "Remove a listener from an ALB.",
    outcomeTemplate: JSON.stringify({ listener_arn: "", rollback_strategy: "rollback_unavailable" }, null, 2),
  },
  gce_instance_create: {
    label: "Launch GCE Instance",
    description: "Create a new Compute Engine VM with agent, IAP, or SSH connection.",
    outcomeTemplate: JSON.stringify({
      name: "",
      machine_type: "e2-micro",
      zone: "us-central1-a",
      image_family: "ubuntu-2204-lts",
      image_project: "ubuntu-os-cloud",
      connection_mode: "agent_startup",
      rollback_strategy: "delete_instance",
    }, null, 2),
  },
  gce_stop: {
    label: "Stop GCE Instance",
    description: "Gracefully stop a running Compute Engine instance.",
    outcomeTemplate: JSON.stringify({ instance_name: "", zone: "us-central1-a", rollback_strategy: "start_instance" }, null, 2),
  },
  gce_start: {
    label: "Start GCE Instance",
    description: "Start a stopped Compute Engine instance.",
    outcomeTemplate: JSON.stringify({ instance_name: "", zone: "us-central1-a", rollback_strategy: "stop_instance" }, null, 2),
  },
  gce_instance_reboot: {
    label: "Reboot GCE Instance",
    description: "Hard reset (reset) a Compute Engine instance.",
    outcomeTemplate: JSON.stringify({ instance_name: "", zone: "us-central1-a", rollback_strategy: "rollback_unavailable" }, null, 2),
  },
  gce_instance_delete: {
    label: "Delete GCE Instance",
    description: "Permanently delete a Compute Engine instance.",
    outcomeTemplate: JSON.stringify({ instance_name: "", zone: "us-central1-a", rollback_strategy: "rollback_unavailable" }, null, 2),
  },
  gce_disk_snapshot: {
    label: "Create GCE Disk Snapshot",
    description: "Snapshot the boot disk of a Compute Engine instance.",
    outcomeTemplate: JSON.stringify({ instance_name: "", zone: "us-central1-a", snapshot_name: "", rollback_strategy: "delete_disk_snapshot" }, null, 2),
  },
  azure_vm_create: {
    label: "Launch Azure VM",
    description: "Create a new Azure VM with agent, SSH, or password connection.",
    outcomeTemplate: JSON.stringify({ vm_name: "", resource_group: "", location: "eastus", vm_size: "Standard_B1s", connection_mode: "agent_extension", rollback_strategy: "terminate_vm" }, null, 2),
  },
  azure_vm_stop: {
    label: "Stop Azure VM",
    description: "Deallocate a running Azure VM to halt compute charges.",
    outcomeTemplate: JSON.stringify({ vm_name: "", resource_group: "", rollback_strategy: "start_vm" }, null, 2),
  },
  azure_vm_start: {
    label: "Start Azure VM",
    description: "Start a deallocated Azure VM.",
    outcomeTemplate: JSON.stringify({ vm_name: "", resource_group: "", rollback_strategy: "deallocate_vm" }, null, 2),
  },
  azure_vm_reboot: {
    label: "Reboot Azure VM",
    description: "Restart a running Azure VM.",
    outcomeTemplate: JSON.stringify({ vm_name: "", resource_group: "", rollback_strategy: "rollback_unavailable" }, null, 2),
  },
  azure_vm_delete: {
    label: "Delete Azure VM",
    description: "Permanently delete an Azure VM and its network resources.",
    outcomeTemplate: JSON.stringify({ vm_name: "", resource_group: "", rollback_strategy: "rollback_unavailable" }, null, 2),
  },
  azure_vm_snapshot: {
    label: "Create Azure Disk Snapshot",
    description: "Snapshot the OS disk of an Azure VM.",
    outcomeTemplate: JSON.stringify({ vm_name: "", resource_group: "", snapshot_name: "", rollback_strategy: "delete_disk_snapshot" }, null, 2),
  },
  azure_run_command: {
    label: "Run Command on Azure VM",
    description: "Execute a shell command via Azure Run Command (no SSH required).",
    outcomeTemplate: JSON.stringify({ vm_name: "", resource_group: "", command: "", rollback_strategy: "rollback_unavailable" }, null, 2),
  },
  generic_remediation: {
    label: "Generic Remediation",
    description: "Generic remediation action for scanner findings.",
    outcomeTemplate: JSON.stringify({ finding_id: null, action: "remediate", rollback_strategy: "rollback_unavailable" }, null, 2),
  },
  notify_only: {
    label: "Notify Only",
    description: "Create a record and notification without executing any change.",
    outcomeTemplate: JSON.stringify({ message: "", rollback_strategy: "rollback_unavailable" }, null, 2),
  },
  suppress: {
    label: "Suppress Finding",
    description: "Suppress a scanner finding with a justification.",
    outcomeTemplate: JSON.stringify({ finding_id: null, reason: "", expiry_days: 90, rollback_strategy: "unsuppress_finding" }, null, 2),
  },
  ip_campaign: {
    label: "IP Patch Campaign",
    description: "Rolling patch campaign for in-place (IP) updates — applies OS-level patches without replacing instances.",
    outcomeTemplate: JSON.stringify({ cve_id: null, packages: [], batch_size_pct: 10, abort_threshold_pct: 25, dry_run: false, rollback_strategy: "uninstall_patches" }, null, 2),
  },
  agent_appdiscovery: {
    label: "Discover Applications",
    description: "Scan a host for running services and register them as application assets in the inventory.",
    outcomeTemplate: JSON.stringify({ rollback_strategy: "rollback_unavailable" }, null, 2),
  },
  agent_containerize_build: {
    label: "Build Container Image",
    description: "Build a Docker image from a discovered application workload and push to a registry.",
    outcomeTemplate: JSON.stringify({ registry: "", namespace: "nexplane-migrations", dry_run: false, rollback_strategy: "rollback_unavailable" }, null, 2),
  },
  agent_containerize_retire: {
    label: "Retire Legacy Service",
    description: "Stop and remove the original legacy service after a successful container migration soak window.",
    outcomeTemplate: JSON.stringify({ rollback_strategy: "rollback_unavailable" }, null, 2),
  },
  agent_containerize_auto: {
    label: "Autonomous Containerization ✨ AI",
    description: "AI-directed migration from legacy services to Kubernetes. Discovers workloads, maps dependencies (including brownfield containers), builds and deploys containers, verifies with a configurable soak window. Retirement requires human approval. Requires AI provider configured.",
    outcomeTemplate: JSON.stringify({
      registry: "",
      target_cluster_id: "",
      namespace: "nexplane-migrations",
      soak_seconds: 120,
      dry_run: false,
    }, null, 2),
  },
  oci_instance_create: {
    label: "Launch OCI Instance ✨ AI",
    description: "Create a new Oracle Cloud compute instance (VM.Standard.E2.1.Micro, always-free eligible). Auto-resolves subnet. Rollback: terminate.",
    outcomeTemplate: JSON.stringify({
      name: "nexplane-oci-instance",
      mode: "quick",
      os: "oracle_linux",
      shape: "VM.Standard.E2.1.Micro",
      ocpus: 1,
      memory_in_gbs: 1,
      compartment_id: "",
      subnet_id: "",
      ssh_public_key: "",
      rollback_strategy: "terminate_instance",
    }, null, 2),
  },
  oci_instance_stop: {
    label: "Stop OCI Instance",
    description: "Gracefully stop a running OCI compute instance. Rollback: start.",
    outcomeTemplate: JSON.stringify({ instance_id: "", rollback_strategy: "start_instance" }, null, 2),
  },
  oci_instance_start: {
    label: "Start OCI Instance",
    description: "Start a stopped OCI compute instance. Rollback: stop.",
    outcomeTemplate: JSON.stringify({ instance_id: "", rollback_strategy: "stop_instance" }, null, 2),
  },
  oci_instance_reboot: {
    label: "Reboot OCI Instance",
    description: "Graceful reboot (SOFTRESET) of a running OCI compute instance.",
    outcomeTemplate: JSON.stringify({ instance_id: "", rollback_strategy: "rollback_unavailable" }, null, 2),
  },
  oci_instance_delete: {
    label: "Terminate OCI Instance",
    description: "Permanently terminate an OCI compute instance. This is destructive.",
    outcomeTemplate: JSON.stringify({ instance_id: "", preserve_boot_volume: false, rollback_strategy: "rollback_unavailable" }, null, 2),
  },
  oci_block_volume_snapshot: {
    label: "Create OCI Boot Volume Snapshot",
    description: "Create an incremental boot volume backup for an OCI instance. Rollback: delete backup.",
    outcomeTemplate: JSON.stringify({ instance_id: "", display_name: "nexplane-snapshot", backup_type: "INCREMENTAL", rollback_strategy: "delete_boot_volume_backup" }, null, 2),
  },
  oci_vcn_create: {
    label: "Create OCI VCN",
    description: "Create an OCI Virtual Cloud Network with internet gateway and route table. Rollback: delete VCN.",
    outcomeTemplate: JSON.stringify({ compartment_id: "", display_name: "nexplane-vcn", cidr_block: "10.0.0.0/16", dns_label: "nexplanevcn", rollback_strategy: "delete_vcn" }, null, 2),
  },
  oci_subnet_create: {
    label: "Create OCI Subnet",
    description: "Create an OCI subnet with SSH + ICMP security list. Rollback: delete subnet.",
    outcomeTemplate: JSON.stringify({ compartment_id: "", vcn_id: "", display_name: "nexplane-subnet", cidr_block: "10.0.0.0/24", dns_label: "nexplanesubnet", prohibit_public_ip_on_vnic: false, rollback_strategy: "delete_subnet" }, null, 2),
  },
  oci_bucket_create: {
    label: "Create Object Storage Bucket",
    description: "Create a new OCI Object Storage bucket.",
    outcomeTemplate: JSON.stringify({
      compartment_id: "ocid1.compartment.oc1..aaaa",
      name: "nexplane-bucket",
      storage_tier: "Standard",
      public_access_type: "NoPublicAccess",
      versioning: "Disabled",
      rollback_strategy: "delete_bucket",
    }, null, 2),
  },
  oci_bucket_delete: {
    label: "Delete Object Storage Bucket",
    description: "Delete an OCI Object Storage bucket (must be empty).",
    outcomeTemplate: JSON.stringify({
      bucket_name: "",
      namespace: "",
      rollback_strategy: "rollback_unavailable",
    }, null, 2),
  },
  oci_bucket_lifecycle_set: {
    label: "Configure Bucket Lifecycle",
    description: "Set lifecycle rules on an OCI Object Storage bucket.",
    outcomeTemplate: JSON.stringify({
      bucket_name: "",
      namespace: "",
      rules: [{ name: "expire-objects", action: "DELETE", time_amount: 90, time_unit: "DAYS", is_enabled: true }],
      rollback_strategy: "clear_lifecycle_rules",
    }, null, 2),
  },
  oci_bucket_block_public: {
    label: "Block Bucket Public Access",
    description: "Set public_access_type to NoPublicAccess on an OCI bucket.",
    outcomeTemplate: JSON.stringify({
      bucket_name: "",
      namespace: "",
      rollback_strategy: "restore_previous_access_type",
    }, null, 2),
  },
  oci_block_volume_create: {
    label: "Create Block Volume",
    description: "Create an OCI Block Volume.",
    outcomeTemplate: JSON.stringify({
      compartment_id: "ocid1.compartment.oc1..aaaa",
      display_name: "nexplane-volume",
      size_in_gbs: 50,
      vpus_per_gb: 10,
      availability_domain: "",
      rollback_strategy: "delete_block_volume",
    }, null, 2),
  },
  oci_block_volume_attach: {
    label: "Attach Block Volume",
    description: "Attach an OCI Block Volume to a compute instance. Requires instance asset + block-volume asset as targets.",
    outcomeTemplate: JSON.stringify({
      instance_id: "",
      volume_id: "",
      display_name: "nexplane-attachment",
      type: "paravirtualized",
      is_read_only: false,
      rollback_strategy: "detach_block_volume",
    }, null, 2),
  },
  oci_block_volume_detach: {
    label: "Detach Block Volume",
    description: "Detach an OCI Block Volume from a compute instance. Requires instance asset + block-volume asset as targets.",
    outcomeTemplate: JSON.stringify({
      instance_id: "",
      volume_id: "",
      rollback_strategy: "reattach_block_volume",
    }, null, 2),
  },
  oci_block_volume_delete: {
    label: "Delete Block Volume",
    description: "Delete an OCI Block Volume (must be detached first).",
    outcomeTemplate: JSON.stringify({
      volume_id: "",
      rollback_strategy: "rollback_unavailable",
    }, null, 2),
  },
  oci_block_volume_backup: {
    label: "Create Block Volume Backup",
    description: "Create an incremental backup of an OCI Block Volume.",
    outcomeTemplate: JSON.stringify({
      volume_id: "",
      display_name: "nexplane-backup",
      type: "INCREMENTAL",
      rollback_strategy: "delete_backup",
    }, null, 2),
  },
  oci_security_list_add_rule: {
    label: "OCI Add Security List Rule",
    description: "Add an inbound or outbound rule to an OCI Security List.",
    outcomeTemplate: JSON.stringify({
      security_list_id: "",
      direction: "INGRESS",
      protocol: "6",
      source: "0.0.0.0/0",
      port_min: 22,
      port_max: 22,
      description: "nexplane-rule",
      rollback_strategy: "oci_security_list_remove_rule",
    }, null, 2),
  },
  oci_security_list_remove_rule: {
    label: "OCI Remove Security List Rule",
    description: "Remove an inbound or outbound rule from an OCI Security List.",
    outcomeTemplate: JSON.stringify({
      security_list_id: "",
      direction: "INGRESS",
      protocol: "6",
      source: "0.0.0.0/0",
      port_min: 22,
      port_max: 22,
      rollback_strategy: "oci_security_list_add_rule",
    }, null, 2),
  },
  oci_nsg_create: {
    label: "OCI Create NSG",
    description: "Create a Network Security Group in an OCI VCN.",
    outcomeTemplate: JSON.stringify({
      compartment_id: "",
      vcn_id: "",
      display_name: "nexplane-nsg",
      rollback_strategy: "oci_nsg_delete",
    }, null, 2),
  },
  oci_nsg_delete: {
    label: "OCI Delete NSG",
    description: "Delete an OCI Network Security Group. Irreversible.",
    outcomeTemplate: JSON.stringify({
      nsg_id: "",
      rollback_strategy: "rollback_unavailable",
    }, null, 2),
  },
  oci_nsg_rule_add: {
    label: "OCI Add NSG Rule",
    description: "Add a security rule to an OCI NSG.",
    outcomeTemplate: JSON.stringify({
      nsg_id: "",
      direction: "INGRESS",
      protocol: "6",
      source: "0.0.0.0/0",
      port_min: 443,
      port_max: 443,
      description: "nexplane-nsg-rule",
      rollback_strategy: "oci_nsg_rule_remove",
    }, null, 2),
  },
  oci_nsg_rule_remove: {
    label: "OCI Remove NSG Rule",
    description: "Remove a rule from an OCI NSG.",
    outcomeTemplate: JSON.stringify({
      nsg_id: "",
      rule_id: "",
      rollback_strategy: "oci_nsg_rule_add",
    }, null, 2),
  },
  oci_load_balancer_create: {
    label: "OCI Create Load Balancer",
    description: "Create an OCI flexible Load Balancer. Polls up to 15 min until ACTIVE.",
    outcomeTemplate: JSON.stringify({
      compartment_id: "",
      display_name: "nexplane-lb",
      shape_name: "flexible",
      shape_min_mbps: 10,
      shape_max_mbps: 100,
      subnet_ids: [],
      is_private: false,
      rollback_strategy: "oci_load_balancer_delete",
    }, null, 2),
  },
  oci_load_balancer_delete: {
    label: "OCI Delete Load Balancer",
    description: "Delete an OCI Load Balancer. Irreversible.",
    outcomeTemplate: JSON.stringify({
      load_balancer_id: "",
      rollback_strategy: "rollback_unavailable",
    }, null, 2),
  },
  oci_backend_set_create: {
    label: "OCI Create Backend Set",
    description: "Create a backend set on an OCI Load Balancer.",
    outcomeTemplate: JSON.stringify({
      load_balancer_id: "",
      name: "nexplane-backend-set",
      policy: "ROUND_ROBIN",
      health_checker: {
        protocol: "HTTP",
        port: 80,
        url_path: "/health",
        return_code: 200,
        interval_ms: 10000,
        timeout_in_millis: 3000,
        retries: 3,
      },
      rollback_strategy: "delete_backend_set",
    }, null, 2),
  },
  oci_listener_create: {
    label: "OCI Create Listener",
    description: "Create a listener on an OCI Load Balancer.",
    outcomeTemplate: JSON.stringify({
      load_balancer_id: "",
      name: "nexplane-listener",
      default_backend_set: "nexplane-backend-set",
      port: 80,
      protocol: "HTTP",
      rollback_strategy: "delete_listener",
    }, null, 2),
  },
  oci_dns_zone_create: {
    label: "OCI Create DNS Zone",
    description: "Create an OCI DNS zone of type PRIMARY.",
    outcomeTemplate: JSON.stringify({
      compartment_id: "",
      name: "nexplane-test.example.com",
      zone_type: "PRIMARY",
      rollback_strategy: "oci_dns_zone_delete",
    }, null, 2),
  },
  oci_dns_record_upsert: {
    label: "OCI Upsert DNS Record",
    description: "Create or update an A, CNAME, or other DNS record in an OCI DNS zone.",
    outcomeTemplate: JSON.stringify({
      zone_name_or_id: "",
      domain: "www.nexplane-test.example.com",
      rtype: "A",
      ttl: 300,
      rdata: "10.0.0.1",
      rollback_strategy: "restore_previous_record",
    }, null, 2),
  },
  // Oracle Cloud — Identity
  oci_iam_user_create: {
    label: "Create OCI IAM User",
    description: "Create an OCI IAM user (tenancy-scoped). Optionally assign to a group.",
    outcomeTemplate: JSON.stringify({
      name: "nexplane-user",
      description: "Created by Nexplane",
      email: "",
      group_id: "",
      rollback_strategy: "oci_iam_user_delete",
    }, null, 2),
  },
  oci_iam_user_delete: {
    label: "Delete OCI IAM User",
    description: "Remove all group memberships and permanently delete an OCI IAM user.",
    outcomeTemplate: JSON.stringify({
      user_id: "ocid1.user.oc1..",
      rollback_strategy: "rollback_unavailable",
    }, null, 2),
  },
  oci_iam_user_disable: {
    label: "Disable OCI IAM User",
    description: "Block console login and API key access for an OCI IAM user.",
    outcomeTemplate: JSON.stringify({
      user_id: "ocid1.user.oc1..",
      rollback_strategy: "oci_iam_user_enable",
    }, null, 2),
  },
  oci_iam_user_enable: {
    label: "Enable OCI IAM User",
    description: "Restore console login and API key access for an OCI IAM user.",
    outcomeTemplate: JSON.stringify({
      user_id: "ocid1.user.oc1..",
      can_use_console_password: true,
      can_use_api_keys: true,
      rollback_strategy: "oci_iam_user_disable",
    }, null, 2),
  },
  oci_iam_group_create: {
    label: "Create OCI IAM Group",
    description: "Create an OCI IAM group and optionally add users.",
    outcomeTemplate: JSON.stringify({
      name: "nexplane-group",
      description: "Created by Nexplane",
      user_ids: [],
      rollback_strategy: "oci_iam_group_delete",
    }, null, 2),
  },
  oci_iam_group_delete: {
    label: "Delete OCI IAM Group",
    description: "Remove all memberships and permanently delete an OCI IAM group.",
    outcomeTemplate: JSON.stringify({
      group_id: "ocid1.group.oc1..",
      rollback_strategy: "rollback_unavailable",
    }, null, 2),
  },
  oci_iam_policy_create: {
    label: "Create OCI IAM Policy",
    description: "Create an OCI IAM policy with policy statements.",
    outcomeTemplate: JSON.stringify({
      compartment_id: "",
      name: "nexplane-policy",
      description: "Created by Nexplane",
      statements: ["Allow group nexplane-group to read all-resources in tenancy"],
      rollback_strategy: "oci_iam_policy_delete",
    }, null, 2),
  },
  oci_iam_policy_delete: {
    label: "Delete OCI IAM Policy",
    description: "Permanently delete an OCI IAM policy.",
    outcomeTemplate: JSON.stringify({
      policy_id: "ocid1.policy.oc1..",
      rollback_strategy: "rollback_unavailable",
    }, null, 2),
  },
  oci_vault_secret_create: {
    label: "Create OCI Vault Secret",
    description: "Create a secret in OCI Vault. Requires an ACTIVE vault in the compartment.",
    outcomeTemplate: JSON.stringify({
      compartment_id: "",
      vault_id: "",
      key_id: "",
      secret_name: "nexplane-secret",
      secret_content: "changeme",
      description: "Created by Nexplane",
      rollback_strategy: "oci_vault_secret_delete",
    }, null, 2),
  },
  oci_vault_secret_delete: {
    label: "Delete OCI Vault Secret",
    description: "Schedule an OCI Vault secret for deferred deletion (minimum 1 day).",
    outcomeTemplate: JSON.stringify({
      secret_id: "ocid1.vaultsecret.oc1..",
      deletion_time_days: 1,
      rollback_strategy: "cancel_vault_secret_deletion",
    }, null, 2),
  },
  oci_compartment_create: {
    label: "Create OCI Compartment",
    description: "Create a child compartment under the target compartment.",
    outcomeTemplate: JSON.stringify({
      parent_compartment_id: "",
      name: "nexplane-compartment",
      description: "Created by Nexplane",
      rollback_strategy: "oci_compartment_delete",
    }, null, 2),
  },
  oci_compartment_delete: {
    label: "Delete OCI Compartment",
    description: "Delete an OCI compartment. Compartment must be empty.",
    outcomeTemplate: JSON.stringify({
      compartment_id: "ocid1.compartment.oc1..",
      rollback_strategy: "rollback_unavailable",
    }, null, 2),
  },
  // Oracle Cloud — Database + Observability (SP5)
  oci_adb_create: {
    label: "Create Autonomous Database",
    description: "Provision an OCI Autonomous Database (Always Free tier). ADB provisioning takes up to 15 minutes.",
    outcomeTemplate: JSON.stringify({
      compartment_id: "",
      display_name: "nexplane-adb",
      db_name: "nexplaneadb",
      admin_password: "Nexplane1234!",
      db_workload: "OLTP",
      cpu_core_count: 1,
      data_storage_size_in_tbs: 1,
      is_auto_scaling_enabled: false,
      is_free_tier: true,
      license_model: "LICENSE_INCLUDED",
      rollback_strategy: "oci_adb_delete",
    }, null, 2),
  },
  oci_adb_stop: {
    label: "Stop Autonomous Database",
    description: "Stop a running OCI Autonomous Database instance.",
    outcomeTemplate: JSON.stringify({
      db_id: "",
      rollback_strategy: "oci_adb_start",
    }, null, 2),
  },
  oci_adb_start: {
    label: "Start Autonomous Database",
    description: "Start a stopped OCI Autonomous Database instance.",
    outcomeTemplate: JSON.stringify({
      db_id: "",
      rollback_strategy: "oci_adb_stop",
    }, null, 2),
  },
  oci_adb_delete: {
    label: "Delete Autonomous Database",
    description: "Permanently terminate an OCI Autonomous Database instance. This action is irreversible.",
    outcomeTemplate: JSON.stringify({
      db_id: "",
      rollback_strategy: "rollback_unavailable",
    }, null, 2),
  },
  oci_adb_backup: {
    label: "Create ADB Manual Backup",
    description: "Create a manual backup of an OCI Autonomous Database. Backup activation can take up to 30 minutes.",
    outcomeTemplate: JSON.stringify({
      db_id: "",
      display_name: "nexplane-backup",
      rollback_strategy: "delete_backup",
    }, null, 2),
  },
  oci_mysql_create: {
    label: "Create MySQL HeatWave DB System",
    description: "Provision an OCI MySQL HeatWave DB System. MySQL provisioning takes up to 20 minutes.",
    outcomeTemplate: JSON.stringify({
      compartment_id: "",
      display_name: "nexplane-mysql",
      admin_username: "nexplane",
      admin_password: "Nexplane1234!",
      shape_name: "MySQL.VM.Standard.E4.1.8GB",
      mysql_version: "8.0.36",
      subnet_id: "",
      data_storage_size_in_gbs: 50,
      availability_domain: "",
      rollback_strategy: "oci_mysql_delete",
    }, null, 2),
  },
  oci_mysql_stop: {
    label: "Stop MySQL HeatWave DB System",
    description: "Stop a running OCI MySQL HeatWave DB System.",
    outcomeTemplate: JSON.stringify({
      db_system_id: "",
      rollback_strategy: "oci_mysql_start",
    }, null, 2),
  },
  oci_mysql_start: {
    label: "Start MySQL HeatWave DB System",
    description: "Start a stopped OCI MySQL HeatWave DB System.",
    outcomeTemplate: JSON.stringify({
      db_system_id: "",
      rollback_strategy: "oci_mysql_stop",
    }, null, 2),
  },
  oci_mysql_delete: {
    label: "Delete MySQL HeatWave DB System",
    description: "Delete an OCI MySQL HeatWave DB System. This action is irreversible.",
    outcomeTemplate: JSON.stringify({
      db_system_id: "",
      rollback_strategy: "rollback_unavailable",
    }, null, 2),
  },
  oci_alarm_create: {
    label: "Create OCI Monitoring Alarm",
    description: "Create an OCI Monitoring alarm. Uses MQL query syntax — e.g. CpuUtilization[1m].mean() > 80.",
    outcomeTemplate: JSON.stringify({
      compartment_id: "",
      display_name: "nexplane-alarm",
      namespace: "oci_computeagent",
      query: "CpuUtilization[1m].mean() > 80",
      severity: "CRITICAL",
      body: "CPU utilization exceeded 80%",
      destinations: [],
      is_enabled: true,
      rollback_strategy: "oci_alarm_delete",
    }, null, 2),
  },
  oci_alarm_delete: {
    label: "Delete OCI Monitoring Alarm",
    description: "Delete an OCI Monitoring alarm by its OCID.",
    outcomeTemplate: JSON.stringify({
      alarm_id: "",
      rollback_strategy: "rollback_unavailable",
    }, null, 2),
  },
  oci_logging_enable: {
    label: "Enable OCI Logging",
    description: "Create an OCI Log Group and Log to enable compartment-level audit logging.",
    outcomeTemplate: JSON.stringify({
      compartment_id: "",
      log_group_name: "nexplane-logs",
      log_name: "nexplane-audit-log",
      log_type: "AUDIT",
      is_enabled: true,
      retention_duration: 30,
      rollback_strategy: "disable_logging",
    }, null, 2),
  },
  // Identity — extended
  emergency_user_lockout: {
    label: "Emergency User Lockout",
    description: "Immediately lock a user account across all connected identity systems (emergency response).",
    outcomeTemplate: JSON.stringify({ user_email: "user@example.com", rollback_strategy: "re_enable_account" }, null, 2),
  },
  user_suspension: {
    label: "Suspend User",
    description: "Suspend a user account (reversible) across identity providers.",
    outcomeTemplate: JSON.stringify({ user_email: "user@example.com", rollback_strategy: "re_enable_account" }, null, 2),
  },
  user_scope_reduction: {
    label: "Reduce User Scope",
    description: "Remove excess permissions or group memberships from a user account.",
    outcomeTemplate: JSON.stringify({ user_email: "user@example.com", groups_to_remove: [], rollback_strategy: "restore_previous_scope" }, null, 2),
  },
  enforce_mfa: {
    label: "Enforce MFA",
    description: "Force MFA enrollment for a user or group across connected identity providers.",
    outcomeTemplate: JSON.stringify({ target: "user", identifier: "user@example.com", rollback_strategy: "remove_mfa_requirement" }, null, 2),
  },
  azure_ad_disable_user: {
    label: "Azure AD — Disable User",
    description: "Set accountEnabled=false and revoke all active sessions for an Azure AD / Entra ID user.",
    outcomeTemplate: JSON.stringify({ user_identifier: "user@example.com", rollback_strategy: "re_enable_account" }, null, 2),
  },
  azure_ad_create_user: {
    label: "Azure AD — Create User",
    description: "Provision a new user in Azure AD / Entra ID via Microsoft Graph.",
    outcomeTemplate: JSON.stringify({ user_principal_name: "newuser@example.com", display_name: "New User", initial_password: "", rollback_strategy: "deprovision_account" }, null, 2),
  },
  // Credential rotation — extended
  rotate_secrets_manager_secret: {
    label: "Rotate Secrets Manager Secret",
    description: "Rotate an AWS Secrets Manager secret and propagate the new value to consumers.",
    outcomeTemplate: JSON.stringify({ secret_id: "", rotation_lambda_arn: "", rollback_strategy: "restore_previous_secret" }, null, 2),
  },
  rotate_jwt_signing_key: {
    label: "Rotate JWT Signing Key",
    description: "Generate a new JWT signing key and update the signing service.",
    outcomeTemplate: JSON.stringify({ service_name: "", key_algorithm: "RS256", rollback_strategy: "restore_previous_key" }, null, 2),
  },
  // OS hardening — Linux
  configure_seccomp: {
    label: "Configure Seccomp",
    description: "Apply a seccomp syscall filter profile to restrict kernel attack surface.",
    outcomeTemplate: JSON.stringify({ profile_path: "/etc/seccomp/nexplane.json", service_name: "", rollback_strategy: "remove_seccomp_profile" }, null, 2),
  },
  configure_apparmor: {
    label: "Configure AppArmor",
    description: "Load and enforce an AppArmor profile on a target service.",
    outcomeTemplate: JSON.stringify({ profile_path: "/etc/apparmor.d/nexplane", service_name: "", mode: "enforce", rollback_strategy: "disable_apparmor_profile" }, null, 2),
  },
  configure_selinux: {
    label: "Configure SELinux",
    description: "Set SELinux mode (enforcing/permissive) and apply a policy module.",
    outcomeTemplate: JSON.stringify({ mode: "enforcing", policy_module: "", rollback_strategy: "restore_selinux_mode" }, null, 2),
  },
  apply_sysctl_hardening: {
    label: "Apply Sysctl Hardening",
    description: "Apply kernel parameter hardening via sysctl (network stack, memory protections).",
    outcomeTemplate: JSON.stringify({ params: { "net.ipv4.tcp_syncookies": 1, "kernel.randomize_va_space": 2 }, rollback_strategy: "restore_previous_sysctl" }, null, 2),
  },
  configure_host_firewall: {
    label: "Configure Host Firewall",
    description: "Apply iptables / nftables / ufw rules to restrict inbound and outbound traffic.",
    outcomeTemplate: JSON.stringify({ rules: [], default_policy: "deny", rollback_strategy: "restore_firewall_rules" }, null, 2),
  },
  blacklist_kernel_modules: {
    label: "Blacklist Kernel Modules",
    description: "Add kernel modules to modprobe blacklist to prevent loading.",
    outcomeTemplate: JSON.stringify({ modules: ["usb-storage", "firewire-core"], rollback_strategy: "remove_module_blacklist" }, null, 2),
  },
  harden_mount_options: {
    label: "Harden Mount Options",
    description: "Add nodev, nosuid, noexec mount options to /tmp, /var/tmp, and removable media.",
    outcomeTemplate: JSON.stringify({ paths: ["/tmp", "/var/tmp"], options: ["nodev", "nosuid", "noexec"], rollback_strategy: "restore_fstab" }, null, 2),
  },
  deploy_auditd_rules: {
    label: "Deploy Auditd Rules",
    description: "Deploy Linux audit daemon rules for file access, privilege escalation, and syscall auditing.",
    outcomeTemplate: JSON.stringify({ rules_file: "", rollback_strategy: "remove_auditd_rules" }, null, 2),
  },
  setup_file_integrity_monitoring: {
    label: "Setup File Integrity Monitoring",
    description: "Install and configure AIDE or similar FIM tool to detect unauthorized file changes.",
    outcomeTemplate: JSON.stringify({ monitored_paths: ["/etc", "/bin", "/usr/bin"], rollback_strategy: "uninstall_fim" }, null, 2),
  },
  deploy_ebpf_policy: {
    label: "Deploy eBPF Policy",
    description: "Load an eBPF-based security policy (Cilium, Tetragon, Falco) for runtime enforcement.",
    outcomeTemplate: JSON.stringify({ policy_file: "", tool: "falco", rollback_strategy: "unload_ebpf_policy" }, null, 2),
  },
  harden_ssh: {
    label: "Harden SSH",
    description: "Apply SSH server hardening: disable root login, restrict ciphers, enforce key-only auth.",
    outcomeTemplate: JSON.stringify({ disable_root_login: true, allow_password_auth: false, ciphers: [], rollback_strategy: "restore_sshd_config" }, null, 2),
  },
  configure_pam: {
    label: "Configure PAM",
    description: "Apply PAM configuration for password quality, account lockout, and MFA.",
    outcomeTemplate: JSON.stringify({ lockout_attempts: 5, password_min_length: 14, rollback_strategy: "restore_pam_config" }, null, 2),
  },
  seccomp_learn: {
    label: "Seccomp Learning Mode",
    description: "Run a service in seccomp learning mode to generate a least-privilege syscall profile.",
    outcomeTemplate: JSON.stringify({ service_name: "", duration_seconds: 300, output_path: "/etc/seccomp/generated.json", rollback_strategy: "rollback_unavailable" }, null, 2),
  },
  // OS hardening — Windows
  wdac_audit: {
    label: "WDAC Audit Mode",
    description: "Apply Windows Defender Application Control policy in audit mode.",
    outcomeTemplate: JSON.stringify({ policy_xml_path: "", rollback_strategy: "remove_wdac_policy" }, null, 2),
  },
  wdac_enforce: {
    label: "WDAC Enforce Mode",
    description: "Switch a WDAC policy from audit to enforce mode.",
    outcomeTemplate: JSON.stringify({ policy_id: "", rollback_strategy: "wdac_audit_mode" }, null, 2),
  },
  asr_audit: {
    label: "ASR Rules — Audit Mode",
    description: "Enable Attack Surface Reduction rules in audit mode via Microsoft Defender.",
    outcomeTemplate: JSON.stringify({ rule_ids: [], mode: "AuditMode", rollback_strategy: "disable_asr_rules" }, null, 2),
  },
  asr_enforce: {
    label: "ASR Rules — Enforce Mode",
    description: "Switch Attack Surface Reduction rules to block mode.",
    outcomeTemplate: JSON.stringify({ rule_ids: [], mode: "Enabled", rollback_strategy: "asr_audit_mode" }, null, 2),
  },
  sysmon_deploy: {
    label: "Deploy Sysmon",
    description: "Install and configure Sysmon for Windows event logging.",
    outcomeTemplate: JSON.stringify({ config_xml_url: "", rollback_strategy: "uninstall_sysmon" }, null, 2),
  },
  sysmon_fim: {
    label: "Sysmon File Integrity Monitoring",
    description: "Configure Sysmon to monitor specific directories for file changes.",
    outcomeTemplate: JSON.stringify({ monitored_paths: ["C:\\Windows\\System32", "C:\\Program Files"], rollback_strategy: "restore_sysmon_config" }, null, 2),
  },
  platform_upgrade: {
    label: "Platform Upgrade",
    description: "Upgrade the Nexplane backend to a new release version with snapshot, migration, watchdog cutover, and rollback.",
    outcomeTemplate: JSON.stringify({
      target_version: "",
      image_sha256: "",
      changelog_url: "",
      require_approval: "true",
    }, null, 2),
  },
};

const CHANGE_TYPE_GROUPS: { label: string; types: ChangeType[] }[] = [
  {
    label: "Infrastructure",
    types: ["dns_update", "security_group_update", "microsegmentation_policy", "snapshot_asset"],
  },
  {
    label: "EC2",
    types: ["ec2_launch", "ec2_start", "ec2_stop", "ec2_reboot", "ec2_stop_start", "ec2_terminate", "ssm_command", "key_pair_create", "tailscale_join", "tailscale_remove", "deploy_nexplane_agent"],
  },
  {
    label: "Patching",
    types: ["patch_packages", "patch_campaign", "ip_campaign"],
  },
  {
    label: "Identity",
    types: ["offboard_user", "onboard_user", "emergency_user_lockout", "user_suspension", "user_scope_reduction", "enforce_mfa", "azure_ad_disable_user", "azure_ad_create_user"],
  },
  {
    label: "Credential Rotation",
    types: ["rotate_db_credentials", "rotate_ssh_keys", "rotate_api_key", "rotate_service_account", "key_rotation", "rotate_secrets_manager_secret", "rotate_jwt_signing_key"],
  },
  {
    label: "Fleet",
    types: ["rolling_restart", "canary_config_push", "distribute_file", "fleet_health_check"],
  },
  {
    label: "IaC",
    types: ["terraform_local_apply", "ansible_local_playbook", "terraform_apply", "ansible_playbook", "helm_upgrade"],
  },
  {
    label: "Database",
    types: ["provision_db_user", "deprovision_db_user", "db_permission_change", "configure_db_audit", "promote_db_replica", "db_connection_config"],
  },
  {
    label: "IAM",
    types: ["iam_user_create", "iam_user_delete"],
  },
  {
    label: "S3 Storage",
    types: ["s3_bucket_create", "s3_bucket_delete", "s3_lifecycle_configure"],
  },
  {
    label: "DNS (Route53)",
    types: ["route53_zone_create", "route53_record_upsert", "route53_record_delete"],
  },
  {
    label: "RDS",
    types: ["rds_instance_create", "rds_instance_delete", "rds_snapshot_create"],
  },
  {
    label: "Observability",
    types: ["cloudwatch_alarm_create", "cloudwatch_alarm_delete"],
  },
  {
    label: "Load Balancers (ALB)",
    types: ["alb_create", "alb_delete", "target_group_create", "target_group_delete", "register_targets", "deregister_targets", "listener_create", "listener_modify", "listener_delete"],
  },
  {
    label: "GCE Instances",
    types: ["gce_instance_create", "gce_stop", "gce_start", "gce_instance_reboot", "gce_instance_delete", "gce_disk_snapshot"],
  },
  {
    label: "Azure VMs",
    types: ["azure_vm_create", "azure_vm_stop", "azure_vm_start", "azure_vm_reboot",
            "azure_vm_delete", "azure_vm_snapshot", "azure_run_command"],
  },
  {
    label: "Backup & Recovery",
    types: ["create_backup", "verify_backup", "restore_files", "dr_failover", "scheduled_reboot"],
  },
  {
    label: "Compliance",
    types: ["enforce_cis_benchmark", "collect_evidence"],
  },
  {
    label: "OS Hardening — Linux",
    types: [
      "configure_seccomp", "configure_apparmor", "configure_selinux", "apply_sysctl_hardening",
      "configure_host_firewall", "blacklist_kernel_modules", "harden_mount_options", "deploy_auditd_rules",
      "setup_file_integrity_monitoring", "deploy_ebpf_policy", "harden_ssh", "configure_pam", "seccomp_learn",
    ],
  },
  {
    label: "OS Hardening — Windows",
    types: ["wdac_audit", "wdac_enforce", "asr_audit", "asr_enforce", "sysmon_deploy", "sysmon_fim"],
  },
  {
    label: "Containerization",
    types: ["agent_appdiscovery", "agent_containerize_auto", "agent_containerize_build", "agent_containerize_retire"],
  },
  {
    label: "Other",
    types: ["telemetry_agent_deploy", "remote_command", "s3_block_public_access", "iam_enforce_mfa", "generic_remediation", "notify_only", "suppress"],
  },
  {
    label: "Oracle Cloud",
    types: [
      "oci_instance_create",
      "oci_instance_stop",
      "oci_instance_start",
      "oci_instance_reboot",
      "oci_instance_delete",
      "oci_block_volume_snapshot",
      "oci_vcn_create",
      "oci_subnet_create",
      "oci_bucket_create",
      "oci_bucket_delete",
      "oci_bucket_lifecycle_set",
      "oci_bucket_block_public",
      "oci_block_volume_create",
      "oci_block_volume_attach",
      "oci_block_volume_detach",
      "oci_block_volume_delete",
      "oci_block_volume_backup",
      "oci_security_list_add_rule",
      "oci_security_list_remove_rule",
      "oci_nsg_create",
      "oci_nsg_delete",
      "oci_nsg_rule_add",
      "oci_nsg_rule_remove",
      "oci_load_balancer_create",
      "oci_load_balancer_delete",
      "oci_backend_set_create",
      "oci_listener_create",
      "oci_dns_zone_create",
      "oci_dns_record_upsert",
      "oci_iam_user_create",
      "oci_iam_user_delete",
      "oci_iam_user_disable",
      "oci_iam_user_enable",
      "oci_iam_group_create",
      "oci_iam_group_delete",
      "oci_iam_policy_create",
      "oci_iam_policy_delete",
      "oci_vault_secret_create",
      "oci_vault_secret_delete",
      "oci_compartment_create",
      "oci_compartment_delete",
      "oci_adb_create",
      "oci_adb_stop",
      "oci_adb_start",
      "oci_adb_delete",
      "oci_adb_backup",
      "oci_mysql_create",
      "oci_mysql_stop",
      "oci_mysql_start",
      "oci_mysql_delete",
      "oci_alarm_create",
      "oci_alarm_delete",
      "oci_logging_enable",
    ] as ChangeType[],
  },
  {
    label: "Platform",
    types: ["platform_upgrade"] as ChangeType[],
  },
];

// Maps change type → the asset type that should be pre-selected in the filter.
// null means "no restriction — show all assets".
const CHANGE_TYPE_ASSET_FILTER: Partial<Record<ChangeType, AssetType | null>> = {
  // Needs an AWS cloud account to launch into
  key_pair_create: "cloud_account",
  ssm_command: "server",
  ec2_launch: "cloud_account",
  tailscale_join: "server",
  tailscale_remove: "server",
  deploy_nexplane_agent: "server",
  // Operate on existing EC2 servers
  ec2_stop: "server",
  ec2_start: "server",
  ec2_reboot: "server",
  ec2_stop_start: "server",
  ec2_terminate: "server",
  snapshot_asset: "server",
  // Patching targets servers
  patch_packages: "server",
  patch_campaign: "server",
  // Fleet ops target servers
  rolling_restart: "server",
  canary_config_push: "server",
  distribute_file: "server",
  fleet_health_check: "server",
  telemetry_agent_deploy: "server",
  remote_command: "server",
  scheduled_reboot: "server",
  // Compliance on servers
  enforce_cis_benchmark: "server",
  // Backup targets servers or cloud accounts
  create_backup: "server",
  verify_backup: "server",
  restore_files: "server",
  // DNS changes target dns zones
  dns_update: "dns_zone",
  dr_failover: "dns_zone",
  // Firewall changes
  security_group_update: "firewall",
  microsegmentation_policy: "firewall",
  // Identity targets identity assets
  offboard_user: "identity",
  onboard_user: "identity",
  // AWS account-level actions
  terraform_local_apply: "cloud_account",
  ansible_local_playbook: "server",
  s3_block_public_access: "cloud_account",
  iam_enforce_mfa: "cloud_account",
  // New AWS change types
  iam_user_create: "cloud_account",
  iam_user_delete: "identity",
  s3_bucket_create: "cloud_account",
  s3_bucket_delete: "storage_bucket",
  s3_lifecycle_configure: "storage_bucket",
  route53_zone_create: "cloud_account",
  route53_record_upsert: "dns_zone",
  route53_record_delete: "dns_zone",
  rds_instance_create: "cloud_account",
  rds_instance_delete: "database",
  rds_snapshot_create: "database",
  cloudwatch_alarm_create: null,
  cloudwatch_alarm_delete: null,
  // ALB change types
  alb_create: "cloud_account",
  alb_delete: "load_balancer",
  target_group_create: "cloud_account",
  target_group_delete: "load_balancer",
  register_targets: "load_balancer",
  deregister_targets: "load_balancer",
  listener_create: "load_balancer",
  listener_modify: "load_balancer",
  listener_delete: "load_balancer",
  // GCE change types
  gce_instance_create: "cloud_account",
  gce_stop: "server",
  gce_start: "server",
  gce_instance_reboot: "server",
  gce_instance_delete: "server",
  gce_disk_snapshot: "server",
  // Azure change types
  azure_vm_create: "cloud_account",
  azure_vm_stop: "server",
  azure_vm_start: "server",
  azure_vm_reboot: "server",
  azure_vm_delete: "server",
  azure_vm_snapshot: "server",
  azure_run_command: "server",
  // OCI change types
  oci_instance_create: "cloud_account" as AssetType,
  oci_vcn_create: "cloud_account" as AssetType,
  oci_subnet_create: "cloud_account" as AssetType,
  oci_instance_stop: "server" as AssetType,
  oci_instance_start: "server" as AssetType,
  oci_instance_reboot: "server" as AssetType,
  oci_instance_delete: "server" as AssetType,
  oci_block_volume_snapshot: "server" as AssetType,
  oci_bucket_create: "cloud_account" as AssetType,
  oci_bucket_delete: "storage_bucket" as AssetType,
  oci_bucket_lifecycle_set: "storage_bucket" as AssetType,
  oci_bucket_block_public: "storage_bucket" as AssetType,
  oci_block_volume_create: "cloud_account" as AssetType,
  oci_block_volume_attach: "server" as AssetType,
  oci_block_volume_detach: "server" as AssetType,
  oci_block_volume_delete: "storage_bucket" as AssetType,
  oci_block_volume_backup: "storage_bucket" as AssetType,
  oci_security_list_add_rule: "firewall" as AssetType,
  oci_security_list_remove_rule: "firewall" as AssetType,
  oci_nsg_create: "cloud_account" as AssetType,
  oci_nsg_delete: "firewall" as AssetType,
  oci_nsg_rule_add: "firewall" as AssetType,
  oci_nsg_rule_remove: "firewall" as AssetType,
  oci_load_balancer_create: "cloud_account" as AssetType,
  oci_load_balancer_delete: "load_balancer" as AssetType,
  oci_backend_set_create: "load_balancer" as AssetType,
  oci_listener_create: "load_balancer" as AssetType,
  oci_dns_zone_create: "cloud_account" as AssetType,
  oci_dns_record_upsert: "dns_zone" as AssetType,
  // OCI identity — Sub-project 4
  oci_iam_user_create: "cloud_account" as AssetType,
  oci_iam_user_delete: "identity" as AssetType,
  oci_iam_user_disable: "identity" as AssetType,
  oci_iam_user_enable: "identity" as AssetType,
  oci_iam_group_create: "cloud_account" as AssetType,
  oci_iam_group_delete: "application" as AssetType,
  oci_iam_policy_create: "cloud_account" as AssetType,
  oci_iam_policy_delete: "application" as AssetType,
  oci_vault_secret_create: "cloud_account" as AssetType,
  oci_vault_secret_delete: "application" as AssetType,
  oci_compartment_create: "cloud_account" as AssetType,
  oci_compartment_delete: "cloud_account" as AssetType,
  // OCI SP5 — ADB, MySQL, Monitoring, Logging
  oci_adb_create: "cloud_account" as AssetType,
  oci_adb_stop: "database" as AssetType,
  oci_adb_start: "database" as AssetType,
  oci_adb_delete: "database" as AssetType,
  oci_adb_backup: "database" as AssetType,
  oci_mysql_create: "cloud_account" as AssetType,
  oci_mysql_stop: "database" as AssetType,
  oci_mysql_start: "database" as AssetType,
  oci_mysql_delete: "database" as AssetType,
  oci_alarm_create: "cloud_account" as AssetType,
  oci_alarm_delete: "cloud_account" as AssetType,
  oci_logging_enable: "cloud_account" as AssetType,
  // Patching
  ip_campaign: "server",
  // Containerization discovery
  agent_appdiscovery: "server",
  agent_containerize_build: "server",
  agent_containerize_retire: "server",
  // Database actions
  rotate_db_credentials: "database",
  promote_db_replica: "database",
  provision_db_user: "database",
  configure_db_audit: "database",
  deprovision_db_user: "database",
  db_permission_change: "database",
  db_connection_config: "database",
};

const ASSET_TYPE_LABELS: Record<AssetType, string> = {
  server: "server",
  cloud_account: "cloud account",
  dns_zone: "DNS zone",
  firewall: "firewall",
  identity_provider: "identity provider",
  application: "application",
  identity: "identity",
  database: "database",
  storage_bucket: "storage bucket",
  load_balancer: "load balancer",
  endpoint: "endpoint",
  container_cluster: "container cluster",
  key_pair: "key pair",
  kubernetes_cluster: "Kubernetes cluster",
  kubernetes_workload: "Kubernetes workload",
  container_image: "container image",
};

export function CreateChangeRequest() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [searchParams] = useSearchParams();

  const [title, setTitle] = useState(() => searchParams.get("title") ?? "");
  const [description, setDescription] = useState(() => searchParams.get("description") ?? "");
  const [changeType, setChangeType] = useState<ChangeType | "">(() => (searchParams.get("changeType") as ChangeType) ?? "");
  const [selectedAssets, setSelectedAssets] = useState<string[]>(() => searchParams.get("assetId") ? [searchParams.get("assetId")!] : []);
  const [assetSearch, setAssetSearch] = useState("");
  const [autoTypeFilter, setAutoTypeFilter] = useState<AssetType | null>(null);
  const [outcomeJson, setOutcomeJson] = useState("");
  const [jsonError, setJsonError] = useState("");
  const [submitError, setSubmitError] = useState("");
  const [isEmergency, setIsEmergency] = useState(false);
  const [emergencyReason, setEmergencyReason] = useState("");

  const { data: assets } = useQuery({
    queryKey: ["assets"],
    queryFn: () => assetsApi.list(),
  });

  // Pre-fill outcome template and asset filter when arriving from an asset quick action.
  // Runs once assets load so instance_id can be injected from the pre-selected asset.
  useEffect(() => {
    const preType = searchParams.get("changeType") as ChangeType | null;
    if (!preType || !(preType in CHANGE_TYPE_META)) return;

    let template = JSON.parse(CHANGE_TYPE_META[preType]?.outcomeTemplate ?? "{}");

    const preAssetId = searchParams.get("assetId");
    if (preAssetId && assets) {
      const preAsset = assets.find((a) => a.id === preAssetId);
      if (preAsset?.asset_metadata?.instance_id && "instance_id" in template) {
        template = { ...template, instance_id: preAsset.asset_metadata.instance_id as string };
      }
      if (preAsset?.asset_metadata?.instance_name && "instance_name" in template) {
        template = { ...template, instance_name: preAsset.asset_metadata.instance_name as string };
      }
      if (preAsset?.asset_metadata?.zone && "zone" in template) {
        template = { ...template, zone: preAsset.asset_metadata.zone as string };
      }
      if (preAsset?.asset_metadata?.vm_name && "vm_name" in template) {
        template = { ...template, vm_name: preAsset.asset_metadata.vm_name as string };
      }
      if (preAsset?.asset_metadata?.resource_group && "resource_group" in template) {
        template = { ...template, resource_group: preAsset.asset_metadata.resource_group as string };
      }
    }

    setOutcomeJson(JSON.stringify(template, null, 2));
    const filter = CHANGE_TYPE_ASSET_FILTER[preType];
    setAutoTypeFilter(filter !== undefined ? (filter ?? null) : null);
  }, [assets]); // eslint-disable-line react-hooks/exhaustive-deps

  const filteredAssets = (assets ?? []).filter((asset) => {
    if (autoTypeFilter && asset.asset_type !== autoTypeFilter) return false;
    if (!assetSearch.trim()) return true;
    const lower = assetSearch.toLowerCase();
    const tagMatch = lower.match(/tag:(\S+)/);
    if (tagMatch) {
      return (asset.tags ?? []).some((t: string) => t.toLowerCase().includes(tagMatch[1]));
    }
    return (
      asset.name.toLowerCase().includes(lower) ||
      asset.asset_type.toLowerCase().includes(lower) ||
      asset.environment.toLowerCase().includes(lower) ||
      asset.criticality.toLowerCase().includes(lower) ||
      (asset.tags ?? []).some((t: string) => t.toLowerCase().includes(lower))
    );
  });

  const mutation = useMutation({
    mutationFn: () => {
      let outcome: Record<string, unknown>;
      try {
        outcome = JSON.parse(outcomeJson);
      } catch {
        throw new Error("Invalid JSON in Desired Outcome");
      }
      return changeRequestsApi.create({
        title,
        description,
        change_type: changeType as ChangeType,
        target_asset_ids: selectedAssets,
        desired_outcome: outcome,
        priority: isEmergency ? "emergency" : "normal",
        emergency_reason: isEmergency ? emergencyReason : undefined,
      });
    },
    onSuccess: (cr) => {
      qc.invalidateQueries({ queryKey: ["change-requests"] });
      navigate(`/change-requests/${cr.id}`);
    },
    onError: (e: any) => setSubmitError(e.message),
  });

  function handleTypeChange(type: ChangeType) {
    setChangeType(type);
    setOutcomeJson(CHANGE_TYPE_META[type]?.outcomeTemplate ?? "{}");
    setJsonError("");
    const filter = CHANGE_TYPE_ASSET_FILTER[type];
    setAutoTypeFilter(filter !== undefined ? (filter ?? null) : null);
    setSelectedAssets([]);
  }

  function handleJsonChange(value: string) {
    setOutcomeJson(value);
    try {
      JSON.parse(value);
      setJsonError("");
    } catch {
      setJsonError("Invalid JSON");
    }
  }

  const toggleAsset = (id: string) => {
    setSelectedAssets((prev) =>
      prev.includes(id) ? prev.filter((a) => a !== id) : [...prev, id]
    );
  };

  const canSubmit = title && changeType && selectedAssets.length > 0 && outcomeJson && !jsonError;

  return (
    <div className="p-8 max-w-3xl">
      <PageHeader title="New Change Request" subtitle="Submit a governed infrastructure change for safety review and approval" />

      <div className="space-y-6">
        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1.5">Title</label>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="e.g. Update DNS A record for api.acme.example"
            className="w-full text-sm border border-slate-200 rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-500"
          />
        </div>

        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={isEmergency}
              onChange={(e) => setIsEmergency(e.target.checked)}
              className="rounded border-slate-300 text-red-600 focus:ring-red-500"
            />
            <span className="text-sm font-medium text-red-700">Emergency priority</span>
          </label>
          {isEmergency && (
            <span className="text-xs bg-red-100 text-red-700 px-2 py-0.5 rounded-full font-semibold">EMERGENCY</span>
          )}
        </div>
        {isEmergency && (
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1.5">Emergency reason <span className="text-red-600">*</span></label>
            <textarea
              value={emergencyReason}
              onChange={(e) => setEmergencyReason(e.target.value)}
              placeholder="Describe the emergency (e.g. zero-day exploit, active incident)"
              rows={2}
              className="w-full text-sm border border-red-300 rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-red-500 resize-none bg-red-50"
            />
          </div>
        )}

        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1.5">Description</label>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Describe the business reason for this change"
            rows={3}
            className="w-full text-sm border border-slate-200 rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-500 resize-none"
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-slate-700 mb-2">Change Type</label>
          <div className="space-y-4 max-h-96 overflow-y-auto pr-1">
            {CHANGE_TYPE_GROUPS.map((group) => (
              <div key={group.label}>
                <p className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-1.5">{group.label}</p>
                <div className="grid grid-cols-2 gap-2">
                  {group.types.filter((t) => t in CHANGE_TYPE_META).map((type) => (
                    <button
                      key={type}
                      type="button"
                      onClick={() => handleTypeChange(type)}
                      className={`text-left p-3 rounded-lg border text-sm transition-colors ${
                        changeType === type
                          ? "border-brand-500 bg-brand-50 text-brand-800"
                          : "border-slate-200 hover:border-slate-300 text-slate-700"
                      }`}
                    >
                      <div className="font-medium">{CHANGE_TYPE_META[type]?.label ?? type.replace(/_/g, " ")}</div>
                      <div className="text-xs text-slate-400 mt-0.5">{CHANGE_TYPE_META[type]?.description ?? ""}</div>
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>

        <div>
          <label className="block text-sm font-medium text-slate-700 mb-2">Target Assets</label>
          {autoTypeFilter && (
            <div className="flex items-center gap-2 mb-2 px-3 py-1.5 bg-brand-50 border border-brand-200 rounded-md text-xs text-brand-700">
              <span>Showing <strong>{ASSET_TYPE_LABELS[autoTypeFilter]}</strong> assets — required for this change type</span>
              <button onClick={() => setAutoTypeFilter(null)} className="ml-auto text-brand-400 hover:text-brand-700" title="Show all assets">
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
          )}
          <input
            type="text"
            value={assetSearch}
            onChange={(e) => setAssetSearch(e.target.value)}
            placeholder="Filter by name, type, environment, criticality, or tag:pci-scope"
            className="w-full text-sm border border-slate-200 rounded-md px-3 py-2 mb-2 focus:outline-none focus:ring-2 focus:ring-brand-500"
          />
          <div className="space-y-1.5 max-h-48 overflow-y-auto border border-slate-200 rounded-md p-2">
            {filteredAssets.length === 0 && (
              <p className="text-xs text-slate-400 text-center py-3">
                {assetSearch.trim() ? `No assets match "${assetSearch}"` : "No assets found"}
              </p>
            )}
            {filteredAssets.map((asset) => (
              <label key={asset.id} className="flex items-center gap-2.5 p-2 rounded hover:bg-slate-50 cursor-pointer">
                <input
                  type="checkbox"
                  checked={selectedAssets.includes(asset.id)}
                  onChange={() => toggleAsset(asset.id)}
                  className="rounded border-slate-300"
                />
                <div className="flex-1 min-w-0">
                  <div className="text-sm text-slate-900 truncate">{asset.name}</div>
                  <div className="text-xs text-slate-400">
                    {asset.asset_type.replace(/_/g, " ")} · {asset.environment} · {asset.criticality}
                    {(asset.tags ?? []).length > 0 && (
                      <span className="ml-1">· {(asset.tags as string[]).join(", ")}</span>
                    )}
                  </div>
                </div>
              </label>
            ))}
          </div>
        </div>

        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1.5">
            Desired Outcome{" "}
            <span className="text-slate-400 font-normal">(JSON)</span>
          </label>
          <textarea
            value={outcomeJson}
            onChange={(e) => handleJsonChange(e.target.value)}
            rows={12}
            className={`w-full text-xs font-mono border rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-500 resize-none ${
              jsonError ? "border-red-300" : "border-slate-200"
            }`}
            placeholder="Select a change type above to load a template"
          />
          {jsonError && <div className="text-xs text-red-600 mt-1">{jsonError}</div>}
        </div>

        {submitError && (
          <div className="p-3 bg-red-50 border border-red-200 rounded-md text-sm text-red-700">
            {submitError}
          </div>
        )}

        <div className="flex gap-3">
          <button
            type="button"
            onClick={() => mutation.mutate()}
            disabled={!canSubmit || mutation.isPending}
            className="px-4 py-2 bg-brand-600 text-white text-sm font-medium rounded-md hover:bg-brand-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {mutation.isPending ? "Creating..." : "Create Change Request"}
          </button>
          <button
            type="button"
            onClick={() => navigate(-1)}
            className="px-4 py-2 border border-slate-200 text-slate-600 text-sm rounded-md hover:bg-slate-50"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
