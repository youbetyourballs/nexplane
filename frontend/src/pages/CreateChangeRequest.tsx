import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { changeRequestsApi, assetsApi } from "../api/endpoints";
import { PageHeader } from "../components/PageHeader";
import type { ChangeType } from "../types/api";

const CHANGE_TYPE_META: Record<ChangeType, { label: string; description: string; outcomeTemplate: string }> = {
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
    }, null, 2),
  },
  ec2_start: {
    label: "Start EC2 Instance",
    description: "Start a stopped EC2 instance.",
    outcomeTemplate: JSON.stringify({
      instance_id: "i-0123456789abcdef0",
    }, null, 2),
  },
  ec2_reboot: {
    label: "Reboot EC2 Instance",
    description: "Soft reboot — stays on the same host, keeps its public IP.",
    outcomeTemplate: JSON.stringify({
      instance_id: "i-0123456789abcdef0",
    }, null, 2),
  },
  ec2_stop_start: {
    label: "Restart EC2 Instance",
    description: "Full power cycle (stop then start). Instance may get a new public IP if not using an Elastic IP.",
    outcomeTemplate: JSON.stringify({
      instance_id: "i-0123456789abcdef0",
    }, null, 2),
  },
  ec2_launch: {
    label: "Launch EC2 Instance",
    description: "Launch a new instance. Set mode to 'quick' (free-tier defaults), 'clone' (copy existing), or 'spec' (full parameters).",
    outcomeTemplate: JSON.stringify({
      mode: "quick",
      name: "my-new-instance",
      os: "amazon_linux",
    }, null, 2),
  },
  ec2_terminate: {
    label: "Terminate EC2 Instance",
    description: "Permanently terminate an instance. Takes a mandatory snapshot first. Irreversible.",
    outcomeTemplate: JSON.stringify({
      instance_id: "i-0123456789abcdef0",
      snapshot_tag: "pre-terminate-nexplane",
      confirm_terminate: true,
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
    }, null, 2),
  },
  fleet_health_check: {
    label: "Fleet Health Check",
    description: "Run a preflight health check across all hosts: disk, load, pending reboots, service status.",
    outcomeTemplate: JSON.stringify({ asset_group: { asset_ids: [] }, required_services: [] }, null, 2),
  },
  // Patching
  patch_packages: {
    label: "Patch Packages",
    description: "Apply security patches to specific packages or CVEs on target hosts.",
    outcomeTemplate: JSON.stringify({ mode: "security_only", packages: [], cve_id: null, dry_run: false }, null, 2),
  },
  patch_campaign: {
    label: "Patch Campaign",
    description: "Rolling patch campaign across a fleet — batched with abort-on-error threshold.",
    outcomeTemplate: JSON.stringify({ cve_id: "CVE-2024-XXXX", batch_size_pct: 10, abort_threshold_pct: 25, dry_run: false }, null, 2),
  },
  // Identity lifecycle
  offboard_user: {
    label: "Offboard User",
    description: "Disable a user across all connected identity systems (AD, Okta, Google, GitHub, Slack).",
    outcomeTemplate: JSON.stringify({ user_email: "user@example.com", isolate_endpoints: false }, null, 2),
  },
  onboard_user: {
    label: "Onboard User",
    description: "Provision a new user across all connected identity systems.",
    outcomeTemplate: JSON.stringify({ user_email: "newuser@example.com", display_name: "New User", groups: [], role: "member" }, null, 2),
  },
  // Credential rotation
  rotate_db_credentials: {
    label: "Rotate DB Credentials",
    description: "Generate new DB password, update the DB user, update config files, restart service.",
    outcomeTemplate: JSON.stringify({ db_type: "postgres", db_host: "localhost", db_port: 5432, username: "appuser", config_file_path: "/etc/app/.env", service_name: "app" }, null, 2),
  },
  rotate_ssh_keys: {
    label: "Rotate SSH Keys",
    description: "Remove old key by fingerprint from authorized_keys, add new public key across fleet.",
    outcomeTemplate: JSON.stringify({ old_key_fingerprint: "SHA256:...", new_public_key: "ssh-rsa AAAA...", username: "ubuntu" }, null, 2),
  },
  rotate_api_key: {
    label: "Rotate API Key",
    description: "Rotate an API key (AWS IAM, Okta, GitHub PAT) and propagate to consumers.",
    outcomeTemplate: JSON.stringify({ key_type: "aws_iam", username: "svc-account", propagation_targets: [] }, null, 2),
  },
  rotate_service_account: {
    label: "Rotate Service Account",
    description: "Rotate a service account password in AD or Okta and update dependent services.",
    outcomeTemplate: JSON.stringify({ provider: "active_directory", account_name: "svc-app", service_names: [] }, null, 2),
  },
  // Incident response
  isolate_host: {
    label: "Isolate Host",
    description: "Flush outbound firewall rules to isolate a host — allows only management CIDR + control plane.",
    outcomeTemplate: JSON.stringify({ management_cidr: "10.0.0.0/8" }, null, 2),
  },
  lockdown_account: {
    label: "Lockdown Account",
    description: "Lock a user account across all identity systems simultaneously (incident response).",
    outcomeTemplate: JSON.stringify({ user_email: "suspect@example.com" }, null, 2),
  },
  phishing_response: {
    label: "Phishing Response",
    description: "Block sender domain, force password reset, revoke sessions, require MFA re-enrollment.",
    outcomeTemplate: JSON.stringify({ sender_domain: "malicious.example.com", affected_user_emails: [] }, null, 2),
  },
  preserve_evidence: {
    label: "Preserve Evidence",
    description: "Collect forensic artifacts before remediation — logs, netstat, process state → S3.",
    outcomeTemplate: JSON.stringify({ s3_upload_url: null }, null, 2),
  },
  // IaC
  terraform_apply: {
    label: "Terraform Apply",
    description: "Run terraform plan → review in Nexplane → terraform apply on approval.",
    outcomeTemplate: JSON.stringify({ working_directory: "/infra/prod", workspace: "default", var_file: null, target: null }, null, 2),
  },
  ansible_playbook: {
    label: "Ansible Playbook",
    description: "Run an Ansible playbook with --check mode as preflight, then apply on approval.",
    outcomeTemplate: JSON.stringify({ playbook_path: "/playbooks/harden.yml", inventory: "hosts.ini", extra_vars: {}, limit: null }, null, 2),
  },
  helm_upgrade: {
    label: "Helm Upgrade",
    description: "Upgrade a Helm release with --atomic (auto-rollback on failure).",
    outcomeTemplate: JSON.stringify({ release_name: "my-app", chart: "stable/my-app", chart_version: "1.2.0", namespace: "default", values: {} }, null, 2),
  },
  // Database administration
  provision_db_user: {
    label: "Provision DB User",
    description: "Create a DB user with specified grants on PostgreSQL, MySQL, or MSSQL.",
    outcomeTemplate: JSON.stringify({ db_type: "postgres", db_host: "localhost", username: "readonly", grants: ["public.users:SELECT"] }, null, 2),
  },
  deprovision_db_user: {
    label: "Deprovision DB User",
    description: "Revoke all grants and drop a DB user.",
    outcomeTemplate: JSON.stringify({ db_type: "postgres", db_host: "localhost", username: "ex-employee" }, null, 2),
  },
  db_permission_change: {
    label: "DB Permission Change",
    description: "Grant or revoke specific permissions on a database user.",
    outcomeTemplate: JSON.stringify({ db_type: "postgres", db_host: "localhost", target_user: "appuser", grants_to_add: [], grants_to_remove: [] }, null, 2),
  },
  configure_db_audit: {
    label: "Configure DB Audit",
    description: "Enable audit logging (pg_audit, general_log, SQL Audit) on a database.",
    outcomeTemplate: JSON.stringify({ db_type: "postgres", audit_level: "ddl", log_path: "/var/log/pg_audit.log" }, null, 2),
  },
  promote_db_replica: {
    label: "Promote DB Replica",
    description: "Promote an RDS read replica to standalone primary (failover). Irreversible.",
    outcomeTemplate: JSON.stringify({ replica_identifier: "my-db-replica", update_dns_record: true, dns_record: "db.internal.example.com" }, null, 2),
  },
  db_connection_config: {
    label: "DB Connection Config",
    description: "Update max_connections or pg_hba.conf rules.",
    outcomeTemplate: JSON.stringify({ db_type: "postgres", max_connections: 200, reload_only: true }, null, 2),
  },
  // Backup & recovery
  create_backup: {
    label: "Create Backup",
    description: "Create an EBS/RDS snapshot or agent-side restic backup.",
    outcomeTemplate: JSON.stringify({ backup_type: "ebs_snapshot", target_resource_id: "vol-abc123", backup_name: "pre-deploy", retention_days: 30 }, null, 2),
  },
  verify_backup: {
    label: "Verify Backup",
    description: "Restore a backup to a temp environment, run health checks, then destroy.",
    outcomeTemplate: JSON.stringify({ backup_id: "snap-abc123", verification_query: "SELECT 1", terminate_after_verify: true }, null, 2),
  },
  restore_files: {
    label: "Restore Files",
    description: "Restore specific files or directories from a backup snapshot.",
    outcomeTemplate: JSON.stringify({ backup_snapshot_id: "snap-abc123", restore_paths: ["/etc/app"], destination_path: "/etc/app", backup_tool: "restic" }, null, 2),
  },
  dr_failover: {
    label: "DR Failover",
    description: "Fail over to DR site — updates Route53 weighted routing and measures RTO.",
    outcomeTemplate: JSON.stringify({ hosted_zone_id: "Z123ABC", record_name: "api.example.com", dr_target: "api-dr.example.com", target_weight: 100 }, null, 2),
  },
  scheduled_reboot: {
    label: "Scheduled Reboot",
    description: "Schedule a graceful reboot with post-reboot service health verification.",
    outcomeTemplate: JSON.stringify({ reboot_at: "2026-06-01T02:00:00Z", verify_services: ["nginx", "app"] }, null, 2),
  },
  // Compliance
  enforce_cis_benchmark: {
    label: "Enforce CIS Benchmark",
    description: "Audit and remediate CIS controls (Level 1 or 2) on Linux hosts.",
    outcomeTemplate: JSON.stringify({ level: 2, os_family: "debian", dry_run: false }, null, 2),
  },
  collect_evidence: {
    label: "Collect Evidence",
    description: "Gather config files and command outputs for audit evidence (SOC2, PCI, ISO27001).",
    outcomeTemplate: JSON.stringify({ framework: "soc2", control_id: "CC6.1", asset_ids: [] }, null, 2),
  },
  // Misc (keep existing entries that don't fit other categories)
  s3_block_public_access: {
    label: "S3 Block Public Access",
    description: "Enable S3 Block Public Access settings on a bucket.",
    outcomeTemplate: JSON.stringify({ bucket_name: "my-bucket" }, null, 2),
  },
  iam_enforce_mfa: {
    label: "IAM Enforce MFA",
    description: "Enforce MFA requirement on an IAM user or group.",
    outcomeTemplate: JSON.stringify({ target: "user", username: "svc-account" }, null, 2),
  },
  generic_remediation: {
    label: "Generic Remediation",
    description: "Generic remediation action for scanner findings.",
    outcomeTemplate: JSON.stringify({ finding_id: null, action: "remediate" }, null, 2),
  },
  notify_only: {
    label: "Notify Only",
    description: "Create a record and notification without executing any change.",
    outcomeTemplate: JSON.stringify({ message: "" }, null, 2),
  },
  suppress: {
    label: "Suppress Finding",
    description: "Suppress a scanner finding with a justification.",
    outcomeTemplate: JSON.stringify({ finding_id: null, reason: "", expiry_days: 90 }, null, 2),
  },
};

const CHANGE_TYPE_GROUPS: { label: string; types: ChangeType[] }[] = [
  {
    label: "Infrastructure",
    types: ["dns_update", "security_group_update", "microsegmentation_policy", "snapshot_asset"],
  },
  {
    label: "EC2",
    types: ["ec2_launch", "ec2_start", "ec2_stop", "ec2_reboot", "ec2_stop_start", "ec2_terminate"],
  },
  {
    label: "Patching",
    types: ["patch_packages", "patch_campaign"],
  },
  {
    label: "Identity",
    types: ["offboard_user", "onboard_user"],
  },
  {
    label: "Credential Rotation",
    types: ["rotate_db_credentials", "rotate_ssh_keys", "rotate_api_key", "rotate_service_account", "key_rotation"],
  },
  {
    label: "Fleet",
    types: ["rolling_restart", "canary_config_push", "distribute_file", "fleet_health_check"],
  },
  {
    label: "Incident Response",
    types: ["isolate_host", "lockdown_account", "phishing_response", "preserve_evidence"],
  },
  {
    label: "IaC",
    types: ["terraform_apply", "ansible_playbook", "helm_upgrade"],
  },
  {
    label: "Database",
    types: ["provision_db_user", "deprovision_db_user", "db_permission_change", "configure_db_audit", "promote_db_replica", "db_connection_config"],
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
    label: "Other",
    types: ["telemetry_agent_deploy", "remote_command", "s3_block_public_access", "iam_enforce_mfa", "generic_remediation", "notify_only", "suppress"],
  },
];

export function CreateChangeRequest() {
  const navigate = useNavigate();
  const qc = useQueryClient();

  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [changeType, setChangeType] = useState<ChangeType | "">("");
  const [selectedAssets, setSelectedAssets] = useState<string[]>([]);
  const [outcomeJson, setOutcomeJson] = useState("");
  const [jsonError, setJsonError] = useState("");
  const [submitError, setSubmitError] = useState("");

  const { data: assets } = useQuery({
    queryKey: ["assets"],
    queryFn: () => assetsApi.list(),
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
    setOutcomeJson(CHANGE_TYPE_META[type].outcomeTemplate);
    setJsonError("");
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
                      <div className="font-medium">{CHANGE_TYPE_META[type].label}</div>
                      <div className="text-xs text-slate-400 mt-0.5">{CHANGE_TYPE_META[type].description}</div>
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>

        <div>
          <label className="block text-sm font-medium text-slate-700 mb-2">Target Assets</label>
          <div className="space-y-1.5 max-h-48 overflow-y-auto border border-slate-200 rounded-md p-2">
            {(assets ?? []).map((asset) => (
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
