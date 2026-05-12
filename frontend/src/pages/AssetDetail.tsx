import { useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Edit, Save, X, Plus, Zap, Network } from "lucide-react";
import { assetsApi } from "../api/endpoints";
import { changeRequestsApi } from "../api/endpoints";
import { apiClient } from "../api/client";
import { RiskBadge } from "../components/RiskBadge";
import { StatusBadge } from "../components/StatusBadge";
import { PageLoading } from "../components/LoadingSpinner";
import type { Asset, AssetType, Criticality } from "../types/api";
import { IPMigrationWizard } from "../components/IPMigrationWizard";
import { ContainerizationWizard } from "../components/ContainerizationWizard";

// Maps asset type → eligible change types with label + title/description templates
interface QuickAction {
  changeType: string;
  label: string;
  title: (a: Asset) => string;
  description: (a: Asset) => string;
  connectorType?: string;
  condition?: (a: Asset) => boolean;
}

const ASSET_ACTIONS: Record<AssetType, QuickAction[]> = {
  server: [
    {
      changeType: "ec2_reboot",
      label: "Reboot Instance",
      title: (a) => `Reboot ${a.name}`,
      description: (a) => `Reboot EC2 instance ${a.asset_metadata?.instance_id ?? a.name} to apply pending changes.`,
      connectorType: "aws",
    },
    {
      changeType: "ec2_stop",
      label: "Stop Instance",
      title: (a) => `Stop ${a.name}`,
      description: (a) => `Gracefully stop EC2 instance ${a.asset_metadata?.instance_id ?? a.name}.`,
      connectorType: "aws",
    },
    {
      changeType: "ec2_start",
      label: "Start Instance",
      title: (a) => `Start ${a.name}`,
      description: (a) => `Start stopped EC2 instance ${a.asset_metadata?.instance_id ?? a.name}.`,
      connectorType: "aws",
    },
    {
      changeType: "ec2_stop_start",
      label: "Restart Instance",
      title: (a) => `Restart ${a.name}`,
      description: (a) => `Full power cycle of EC2 instance ${a.asset_metadata?.instance_id ?? a.name}.`,
      connectorType: "aws",
    },
    {
      changeType: "ec2_terminate",
      label: "Terminate Instance",
      title: (a) => `Terminate ${a.name}`,
      description: (a) => `Permanently terminate EC2 instance ${a.asset_metadata?.instance_id ?? a.name}. Irreversible.`,
      connectorType: "aws",
    },
    {
      changeType: "snapshot_asset",
      label: "Snapshot",
      title: (a) => `Snapshot ${a.name}`,
      description: (a) => `Create a point-in-time snapshot of ${a.name} (${a.asset_metadata?.instance_id ?? ""}).`,
      connectorType: "aws",
    },
    {
      changeType: "patch_packages",
      label: "Patch Packages",
      title: (a) => `Patch ${a.name}`,
      description: (a) => `Apply security patches to ${a.name}.`,
    },
    {
      changeType: "remote_command",
      label: "Run Command",
      title: (a) => `Run command on ${a.name}`,
      description: (a) => `Execute an approved command template on ${a.name}.`,
    },
    {
      changeType: "isolate_host",
      label: "Isolate Host",
      title: (a) => `Isolate ${a.name}`,
      description: (a) => `Flush outbound firewall rules to isolate ${a.name} from the network.`,
    },
    {
      changeType: "enforce_cis_benchmark",
      label: "Enforce CIS Benchmark",
      title: (a) => `CIS Benchmark on ${a.name}`,
      description: (a) => `Audit and remediate CIS controls on ${a.name}.`,
    },
    {
      changeType: "gce_stop",
      label: "Stop Instance",
      title: (a) => `Stop ${a.name}`,
      description: (a) => `Stop GCE instance ${a.asset_metadata?.instance_name ?? a.name} in zone ${a.asset_metadata?.zone ?? ""}.`,
      connectorType: "gcp",
    },
    {
      changeType: "gce_start",
      label: "Start Instance",
      title: (a) => `Start ${a.name}`,
      description: (a) => `Start stopped GCE instance ${a.asset_metadata?.instance_name ?? a.name}.`,
      connectorType: "gcp",
    },
    {
      changeType: "gce_instance_reboot",
      label: "Reboot Instance",
      title: (a) => `Reboot ${a.name}`,
      description: (a) => `Hard reset GCE instance ${a.asset_metadata?.instance_name ?? a.name}.`,
      connectorType: "gcp",
    },
    {
      changeType: "gce_disk_snapshot",
      label: "Create Disk Snapshot",
      title: (a) => `Snapshot ${a.name}`,
      description: (a) => `Snapshot boot disk of GCE instance ${a.asset_metadata?.instance_name ?? a.name}.`,
      connectorType: "gcp",
    },
    {
      changeType: "gce_instance_delete",
      label: "Delete Instance",
      title: (a) => `Delete ${a.name}`,
      description: (a) => `Permanently delete GCE instance ${a.asset_metadata?.instance_name ?? a.name}. Irreversible.`,
      connectorType: "gcp",
    },
    {
      changeType: "azure_vm_stop",
      label: "Stop VM",
      title: (a) => `Stop ${a.name}`,
      description: (a) => `Deallocate Azure VM ${a.asset_metadata?.vm_name ?? a.name} in ${a.asset_metadata?.resource_group ?? ""}.`,
      connectorType: "azure",
    },
    {
      changeType: "azure_vm_start",
      label: "Start VM",
      title: (a) => `Start ${a.name}`,
      description: (a) => `Start deallocated Azure VM ${a.asset_metadata?.vm_name ?? a.name}.`,
      connectorType: "azure",
    },
    {
      changeType: "azure_vm_reboot",
      label: "Reboot VM",
      title: (a) => `Reboot ${a.name}`,
      description: (a) => `Restart Azure VM ${a.asset_metadata?.vm_name ?? a.name}.`,
      connectorType: "azure",
    },
    {
      changeType: "azure_vm_snapshot",
      label: "Create Disk Snapshot",
      title: (a) => `Snapshot ${a.name}`,
      description: (a) => `Snapshot OS disk of Azure VM ${a.asset_metadata?.vm_name ?? a.name}.`,
      connectorType: "azure",
    },
    {
      changeType: "azure_vm_delete",
      label: "Delete VM",
      title: (a) => `Delete ${a.name}`,
      description: (a) => `Permanently delete Azure VM ${a.asset_metadata?.vm_name ?? a.name}. Irreversible.`,
      connectorType: "azure",
    },
    {
      changeType: "azure_run_command",
      label: "Run Command",
      title: (a) => `Run command on ${a.name}`,
      description: (a) => `Execute a shell command on Azure VM ${a.asset_metadata?.vm_name ?? a.name} via Azure Run Command.`,
      connectorType: "azure",
    },
    {
      changeType: "agent_containerize_auto",
      label: "Migrate to Kubernetes ✨ AI",
      title: (a: Asset) => `Containerize ${a.name}`,
      description: (a: Asset) => `AI-directed autonomous migration of workloads on ${a.name} to Kubernetes. Requires AI provider and Kubernetes connector configured.`,
    },
  ],
  cloud_account: [
    {
      changeType: "ec2_launch",
      label: "Launch EC2 Instance",
      title: (a) => `Launch EC2 in ${a.name}`,
      description: (a) => `Launch a new EC2 instance in AWS account ${a.asset_metadata?.account_id ?? a.name}.`,
      connectorType: "aws",
    },
    {
      changeType: "s3_block_public_access",
      label: "Block S3 Public Access",
      title: (a) => `Block S3 public access in ${a.name}`,
      description: (a) => `Enable S3 Block Public Access settings for account ${a.asset_metadata?.account_id ?? a.name}.`,
      connectorType: "aws",
    },
    {
      changeType: "iam_enforce_mfa",
      label: "Enforce IAM MFA",
      title: (a) => `Enforce MFA in ${a.name}`,
      description: (a) => `Enforce MFA requirement on IAM users in account ${a.asset_metadata?.account_id ?? a.name}.`,
      connectorType: "aws",
    },
    { changeType: "iam_user_create", label: "Create IAM User", title: (a) => `Create IAM user in ${a.name}`, description: (a) => `Create a new IAM user in account ${a.name}.`, connectorType: "aws" },
    { changeType: "s3_bucket_create", label: "Create S3 Bucket", title: (a) => `Create S3 bucket in ${a.name}`, description: (a) => `Create a new S3 bucket in account ${a.name}.`, connectorType: "aws" },
    { changeType: "route53_zone_create", label: "Create Hosted Zone", title: (a) => `Create hosted zone in ${a.name}`, description: (a) => `Create a Route53 hosted zone in account ${a.name}.`, connectorType: "aws" },
    { changeType: "rds_instance_create", label: "Create RDS Instance", title: (a) => `Create RDS instance in ${a.name}`, description: (a) => `Launch a new RDS database instance in account ${a.name}.`, connectorType: "aws" },
    {
      changeType: "gce_instance_create",
      label: "Launch GCE Instance",
      title: (a) => `Launch GCE instance in ${a.name}`,
      description: (a) => `Create a new Compute Engine instance in GCP project ${a.asset_metadata?.project_id ?? a.name}.`,
      connectorType: "gcp",
    },
    {
      changeType: "azure_vm_create",
      label: "Launch Azure VM",
      title: (a) => `Launch Azure VM in ${a.name}`,
      description: (a) => `Create a new Azure VM in subscription ${a.asset_metadata?.subscription_id ?? a.name}.`,
      connectorType: "azure",
    },
  ],
  dns_zone: [
    {
      changeType: "dns_update",
      label: "Update DNS Record",
      title: (a) => `Update DNS record in ${a.name}`,
      description: (a) => `Update a DNS record in zone ${a.name}.`,
    },
    {
      changeType: "dr_failover",
      label: "DR Failover",
      title: (a) => `DR failover for ${a.name}`,
      description: (a) => `Fail over to DR site via Route53 for zone ${a.name}.`,
    },
    { changeType: "route53_record_upsert", label: "Add / Update Record", title: (a) => `Add record to ${a.name}`, description: (a) => `Create or update a DNS record in zone ${a.asset_metadata?.zone_name ?? a.name}.` },
    { changeType: "route53_record_delete", label: "Delete Record", title: (a) => `Delete record from ${a.name}`, description: (a) => `Delete a DNS record from zone ${a.asset_metadata?.zone_name ?? a.name}.` },
  ],
  firewall: [
    {
      changeType: "security_group_update",
      label: "Update Security Group",
      title: (a) => `Update security group on ${a.name}`,
      description: (a) => `Modify firewall rules on ${a.name}.`,
    },
    {
      changeType: "microsegmentation_policy",
      label: "Microsegmentation Policy",
      title: (a) => `Microsegmentation policy for ${a.name}`,
      description: (a) => `Stage a network microsegmentation policy on ${a.name}.`,
    },
  ],
  identity: [
    {
      changeType: "offboard_user",
      label: "Offboard User",
      title: (a) => `Offboard ${a.name}`,
      description: (a) => `Disable ${a.name} across all connected identity systems.`,
    },
    {
      changeType: "lockdown_account",
      label: "Lockdown Account",
      title: (a) => `Lockdown ${a.name}`,
      description: (a) => `Lock ${a.name} across all identity systems immediately.`,
    },
    { changeType: "iam_user_delete", label: "Delete IAM User", title: (a) => `Delete IAM user ${a.name}`, description: (a) => `Delete IAM user ${a.asset_metadata?.username ?? a.name} and all access keys.` },
    {
      changeType: "oci_iam_user_disable",
      label: "Disable OCI User",
      title: (a) => `Disable OCI IAM user ${a.name}`,
      description: (a) => `Block console login and API key access for OCI IAM user ${a.name}.`,
      connectorType: "oci",
      condition: (a) => Array.isArray(a.tags) && a.tags.includes("iam-user") && a.tags.includes("oci"),
    },
    {
      changeType: "oci_iam_user_enable",
      label: "Enable OCI User",
      title: (a) => `Enable OCI IAM user ${a.name}`,
      description: (a) => `Restore console login and API key access for OCI IAM user ${a.name}.`,
      connectorType: "oci",
      condition: (a) => Array.isArray(a.tags) && a.tags.includes("iam-user") && a.tags.includes("oci"),
    },
    {
      changeType: "oci_iam_user_delete",
      label: "Delete OCI User",
      title: (a) => `Delete OCI IAM user ${a.name}`,
      description: (a) => `Remove all group memberships and permanently delete OCI IAM user ${a.name}.`,
      connectorType: "oci",
      condition: (a) => Array.isArray(a.tags) && a.tags.includes("iam-user") && a.tags.includes("oci"),
    },
  ],
  identity_provider: [
    {
      changeType: "rotate_service_account",
      label: "Rotate Service Account",
      title: (a) => `Rotate service account on ${a.name}`,
      description: (a) => `Rotate a service account credential on ${a.name}.`,
    },
  ],
  application: [
    {
      changeType: "helm_upgrade",
      label: "Helm Upgrade",
      title: (a) => `Upgrade ${a.name}`,
      description: (a) => `Upgrade Helm release for ${a.name}.`,
    },
    {
      changeType: "ansible_playbook",
      label: "Ansible Playbook",
      title: (a) => `Run Ansible on ${a.name}`,
      description: (a) => `Run an Ansible playbook against ${a.name}.`,
    },
    {
      changeType: "oci_iam_group_delete",
      label: "Delete OCI Group",
      title: (a) => `Delete OCI IAM group ${a.name}`,
      description: (a) => `Remove all memberships and permanently delete OCI IAM group ${a.name}.`,
      connectorType: "oci",
      condition: (a) => Array.isArray(a.tags) && a.tags.includes("oci-iam-group"),
    },
    {
      changeType: "oci_iam_policy_delete",
      label: "Delete OCI Policy",
      title: (a) => `Delete OCI IAM policy ${a.name}`,
      description: (a) => `Permanently delete OCI IAM policy ${a.name}.`,
      connectorType: "oci",
      condition: (a) => Array.isArray(a.tags) && a.tags.includes("oci-iam-policy"),
    },
    {
      changeType: "oci_vault_secret_delete",
      label: "Delete OCI Secret",
      title: (a) => `Schedule deletion of OCI Vault secret ${a.name}`,
      description: (a) => `Schedule OCI Vault secret ${a.name} for deferred deletion (minimum 1 day).`,
      connectorType: "oci",
      condition: (a) => Array.isArray(a.tags) && a.tags.includes("oci-vault-secret"),
    },
  ],
  database: [
    {
      changeType: "rotate_db_credentials",
      label: "Rotate Credentials",
      title: (a) => `Rotate credentials on ${a.name}`,
      description: (a) => `Rotate database credentials for ${a.name} (${a.asset_metadata?.engine ?? "database"}).`,
    },
    {
      changeType: "create_backup",
      label: "Create Backup",
      title: (a) => `Backup ${a.name}`,
      description: (a) => `Create a backup snapshot of database ${a.name}.`,
    },
    {
      changeType: "provision_db_user",
      label: "Provision DB User",
      title: (a) => `Provision user on ${a.name}`,
      description: (a) => `Create a new database user on ${a.name}.`,
    },
    {
      changeType: "configure_db_audit",
      label: "Configure Audit Logging",
      title: (a) => `Configure audit on ${a.name}`,
      description: (a) => `Enable audit logging on database ${a.name}.`,
    },
    {
      changeType: "promote_db_replica",
      label: "Promote Replica",
      title: (a) => `Promote ${a.name} to primary`,
      description: (a) => `Promote ${a.name} read replica to standalone primary.`,
    },
    { changeType: "rds_snapshot_create", label: "Create RDS Snapshot", title: (a) => `Snapshot ${a.name}`, description: (a) => `Create a manual RDS snapshot of ${a.name}.` },
    { changeType: "rds_instance_delete", label: "Delete Instance", title: (a) => `Delete RDS instance ${a.name}`, description: (a) => `Permanently delete RDS instance ${a.asset_metadata?.db_instance_identifier ?? a.name}.` },
    { changeType: "cloudwatch_alarm_create", label: "Create CloudWatch Alarm", title: (a) => `Monitor ${a.name}`, description: (a) => `Create a CloudWatch alarm for database ${a.name}.` },
    // OCI Autonomous Database quick actions
    {
      changeType: "oci_adb_stop",
      label: "Stop ADB",
      title: (a) => `Stop ADB ${a.name}`,
      description: (a) => `Stop OCI Autonomous Database ${a.name} (${a.asset_metadata?.db_id ?? ""}).`,
      connectorType: "oci",
      condition: (a) => Array.isArray(a.tags) && a.tags.includes("autonomous-database") && !!a.asset_metadata?.db_id,
    },
    {
      changeType: "oci_adb_start",
      label: "Start ADB",
      title: (a) => `Start ADB ${a.name}`,
      description: (a) => `Start stopped OCI Autonomous Database ${a.name} (${a.asset_metadata?.db_id ?? ""}).`,
      connectorType: "oci",
      condition: (a) => Array.isArray(a.tags) && a.tags.includes("autonomous-database") && !!a.asset_metadata?.db_id,
    },
    {
      changeType: "oci_adb_backup",
      label: "Backup ADB",
      title: (a) => `Backup ADB ${a.name}`,
      description: (a) => `Create a manual backup of OCI Autonomous Database ${a.name}.`,
      connectorType: "oci",
      condition: (a) => Array.isArray(a.tags) && a.tags.includes("autonomous-database") && !!a.asset_metadata?.db_id,
    },
    {
      changeType: "oci_adb_delete",
      label: "Delete ADB",
      title: (a) => `Delete ADB ${a.name}`,
      description: (a) => `Permanently terminate OCI Autonomous Database ${a.name}. Irreversible.`,
      connectorType: "oci",
      condition: (a) => Array.isArray(a.tags) && a.tags.includes("autonomous-database") && !!a.asset_metadata?.db_id,
    },
    // OCI MySQL HeatWave quick actions
    {
      changeType: "oci_mysql_stop",
      label: "Stop MySQL",
      title: (a) => `Stop MySQL ${a.name}`,
      description: (a) => `Stop OCI MySQL HeatWave DB System ${a.name} (${a.asset_metadata?.db_system_id ?? ""}).`,
      connectorType: "oci",
      condition: (a) => Array.isArray(a.tags) && a.tags.includes("mysql") && !!a.asset_metadata?.db_system_id,
    },
    {
      changeType: "oci_mysql_start",
      label: "Start MySQL",
      title: (a) => `Start MySQL ${a.name}`,
      description: (a) => `Start stopped OCI MySQL HeatWave DB System ${a.name}.`,
      connectorType: "oci",
      condition: (a) => Array.isArray(a.tags) && a.tags.includes("mysql") && !!a.asset_metadata?.db_system_id,
    },
    {
      changeType: "oci_mysql_delete",
      label: "Delete MySQL",
      title: (a) => `Delete MySQL ${a.name}`,
      description: (a) => `Delete OCI MySQL HeatWave DB System ${a.name}. Irreversible.`,
      connectorType: "oci",
      condition: (a) => Array.isArray(a.tags) && a.tags.includes("mysql") && !!a.asset_metadata?.db_system_id,
    },
  ],
  storage_bucket: [
    {
      changeType: "s3_block_public_access",
      label: "Block Public Access",
      title: (a) => `Block public access on ${a.name}`,
      description: (a) => `Enable S3 Block Public Access on bucket ${a.asset_metadata?.bucket_name ?? a.name}.`,
    },
    { changeType: "s3_lifecycle_configure", label: "Configure Lifecycle", title: (a) => `Configure lifecycle on ${a.name}`, description: (a) => `Set object expiration rules on bucket ${a.asset_metadata?.bucket_name ?? a.name}.` },
    { changeType: "s3_bucket_delete", label: "Delete Bucket", title: (a) => `Delete bucket ${a.name}`, description: (a) => `Empty and delete S3 bucket ${a.asset_metadata?.bucket_name ?? a.name}.` },
    { changeType: "create_backup", label: "Create Backup", title: (a) => `Backup ${a.name}`, description: (a) => `Create a backup of bucket ${a.name}.` },
    // OCI Object Storage actions
    {
      changeType: "oci_bucket_lifecycle_set",
      label: "Configure Lifecycle",
      title: (a) => `Configure lifecycle on ${a.name}`,
      description: (a) => `Set object lifecycle rules on OCI bucket ${a.asset_metadata?.bucket_name ?? a.name}.`,
      connectorType: "oci",
      condition: (a) => Array.isArray(a.tags) && a.tags.includes("object-storage") && !a.tags.includes("block-volume"),
    },
    {
      changeType: "oci_bucket_block_public",
      label: "Block Public Access",
      title: (a) => `Block public access on ${a.name}`,
      description: (a) => `Set public_access_type to NoPublicAccess on OCI bucket ${a.asset_metadata?.bucket_name ?? a.name}.`,
      connectorType: "oci",
      condition: (a) => Array.isArray(a.tags) && a.tags.includes("object-storage") && !a.tags.includes("block-volume"),
    },
    {
      changeType: "oci_bucket_delete",
      label: "Delete Bucket",
      title: (a) => `Delete OCI bucket ${a.name}`,
      description: (a) => `Delete OCI Object Storage bucket ${a.asset_metadata?.bucket_name ?? a.name} (must be empty).`,
      connectorType: "oci",
      condition: (a) => Array.isArray(a.tags) && a.tags.includes("object-storage") && !a.tags.includes("block-volume"),
    },
    // OCI Block Volume actions
    {
      changeType: "oci_block_volume_attach",
      label: "Attach Volume",
      title: (a) => `Attach block volume ${a.name}`,
      description: (a) => `Attach OCI block volume ${a.asset_metadata?.volume_id ?? a.name} to a compute instance.`,
      connectorType: "oci",
      condition: (a) => Array.isArray(a.tags) && (a.tags.includes("block-volume") || a.tags.includes("oci-block-volume")),
    },
    {
      changeType: "oci_block_volume_detach",
      label: "Detach Volume",
      title: (a) => `Detach block volume ${a.name}`,
      description: (a) => `Detach OCI block volume ${a.asset_metadata?.volume_id ?? a.name} from its compute instance.`,
      connectorType: "oci",
      condition: (a) => Array.isArray(a.tags) && (a.tags.includes("block-volume") || a.tags.includes("oci-block-volume")),
    },
    {
      changeType: "oci_block_volume_backup",
      label: "Backup Volume",
      title: (a) => `Backup block volume ${a.name}`,
      description: (a) => `Create an incremental backup of OCI block volume ${a.asset_metadata?.volume_id ?? a.name}.`,
      connectorType: "oci",
      condition: (a) => Array.isArray(a.tags) && (a.tags.includes("block-volume") || a.tags.includes("oci-block-volume")),
    },
    {
      changeType: "oci_block_volume_delete",
      label: "Delete Volume",
      title: (a) => `Delete block volume ${a.name}`,
      description: (a) => `Delete OCI block volume ${a.asset_metadata?.volume_id ?? a.name} (must be detached first).`,
      connectorType: "oci",
      condition: (a) => Array.isArray(a.tags) && (a.tags.includes("block-volume") || a.tags.includes("oci-block-volume")),
    },
  ],
  load_balancer: [
    {
      changeType: "alb_create",
      label: "Create ALB",
      title: (a) => `Create Application Load Balancer`,
      description: () => `Create a new Application Load Balancer in your AWS account.`,
    },
    {
      changeType: "target_group_create",
      label: "Create Target Group",
      title: (a) => `Create target group for ${a.name}`,
      description: (a) => `Create a new target group to register instances behind ${a.name}.`,
    },
    {
      changeType: "register_targets",
      label: "Register Targets",
      title: (a) => `Register targets in ${a.name}`,
      description: (a) => `Register EC2 instances as targets in a target group for ${a.name}.`,
    },
    {
      changeType: "listener_create",
      label: "Add Listener",
      title: (a) => `Add listener to ${a.name}`,
      description: (a) => `Create a new listener (port + protocol) on ${a.name}.`,
    },
    {
      changeType: "listener_modify",
      label: "Modify Listener",
      title: (a) => `Modify listener on ${a.name}`,
      description: (a) => `Update port, protocol, or default action on an existing listener for ${a.name}.`,
    },
    {
      changeType: "alb_delete",
      label: "Delete ALB",
      title: (a) => `Delete ${a.name}`,
      description: (a) => `Permanently delete the ALB ${a.name}. This cannot be undone.`,
    },
    {
      changeType: "security_group_update",
      label: "Update Security Group",
      title: (a) => `Update security group on ${a.name}`,
      description: (a) => `Modify security group rules for load balancer ${a.name}.`,
    },
  ],
  endpoint: [
    {
      changeType: "isolate_host",
      label: "Isolate Host",
      title: (a) => `Isolate ${a.name}`,
      description: (a) => `Network-isolate endpoint ${a.name} (device_id: ${a.asset_metadata?.device_id ?? "unknown"}).`,
    },
    {
      changeType: "patch_packages",
      label: "Patch Packages",
      title: (a) => `Patch ${a.name}`,
      description: (a) => `Apply security patches to endpoint ${a.name}.`,
    },
    {
      changeType: "telemetry_agent_deploy",
      label: "Deploy Agent",
      title: (a) => `Deploy agent to ${a.name}`,
      description: (a) => `Deploy telemetry or security agent to endpoint ${a.name}.`,
    },
    {
      changeType: "remote_command",
      label: "Run Command",
      title: (a) => `Run command on ${a.name}`,
      description: (a) => `Execute an approved command on endpoint ${a.name}.`,
    },
    {
      changeType: "enforce_cis_benchmark",
      label: "Enforce CIS Benchmark",
      title: (a) => `CIS benchmark on ${a.name}`,
      description: (a) => `Audit and remediate CIS controls on endpoint ${a.name}.`,
    },
  ],
  container_cluster: [
    {
      changeType: "helm_upgrade",
      label: "Helm Upgrade",
      title: (a) => `Helm upgrade on ${a.name}`,
      description: (a) => `Upgrade a Helm release on cluster ${a.name}.`,
    },
    {
      changeType: "rolling_restart",
      label: "Rolling Restart",
      title: (a) => `Rolling restart on ${a.name}`,
      description: (a) => `Rolling restart of services on cluster ${a.name}.`,
    },
  ],
  key_pair: [
    {
      changeType: "rotate_ssh_keys",
      label: "Rotate Key",
      title: (a) => `Rotate key pair ${a.name}`,
      description: (a) => `Delete and recreate key pair ${a.asset_metadata?.key_name ?? a.name}.`,
    },
  ],
  kubernetes_cluster: [
    {
      changeType: "helm_upgrade",
      label: "Helm Upgrade",
      title: (a) => `Helm upgrade on ${a.name}`,
      description: (a) => `Upgrade a Helm release on cluster ${a.name}.`,
    },
    {
      changeType: "rolling_restart",
      label: "Rolling Restart",
      title: (a) => `Rolling restart on ${a.name}`,
      description: (a) => `Rolling restart of all workloads on cluster ${a.name}.`,
    },
  ],
  kubernetes_workload: [
    {
      changeType: "helm_upgrade",
      label: "Helm Upgrade",
      title: (a) => `Upgrade ${a.name}`,
      description: (a) => `Upgrade Helm release for workload ${a.name}.`,
    },
    {
      changeType: "rolling_restart",
      label: "Rolling Restart",
      title: (a) => `Restart ${a.name}`,
      description: (a) => `Rolling restart of workload ${a.name}.`,
    },
  ],
  container_image: [
    {
      changeType: "patch_packages",
      label: "Patch Image",
      title: (a) => `Patch ${a.name}`,
      description: (a) => `Apply security patches and rebuild container image ${a.name}.`,
    },
  ],
};

export function AssetDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState("");
  const [editCriticality, setEditCriticality] = useState<Criticality>("medium");
  const [editTags, setEditTags] = useState<string[]>([]);
  const [editMetadata, setEditMetadata] = useState("");
  const [metadataError, setMetadataError] = useState("");
  const [tagInput, setTagInput] = useState("");
  const [showIPWizard, setShowIPWizard] = useState(false);
  const [discoverLoading, setDiscoverLoading] = useState(false);
  const [discoverError, setDiscoverError] = useState<string | null>(null);
  const [showContainerizeWizard, setShowContainerizeWizard] = useState(false);
  const [containerizeApp, setContainerizeApp] = useState<any>(null);

  const { data: asset, isLoading } = useQuery({
    queryKey: ["asset", id],
    queryFn: () => assetsApi.get(id!),
    enabled: !!id,
  });

  const { data: allTags } = useQuery({
    queryKey: ["asset-tags"],
    queryFn: assetsApi.tags,
  });

  const { data: linkedCRs } = useQuery({
    queryKey: ["change-requests", { asset_id: id }],
    queryFn: () => changeRequestsApi.list({ asset_id: id }),
    enabled: !!id,
  });

  const updateMutation = useMutation({
    mutationFn: () => {
      let metadata: Record<string, unknown>;
      try {
        metadata = editMetadata.trim() ? JSON.parse(editMetadata) : {};
      } catch {
        setMetadataError("Invalid JSON");
        throw new Error("Invalid JSON");
      }
      return assetsApi.update(id!, {
        name: editName !== asset?.name ? editName : undefined,
        criticality: editCriticality !== asset?.criticality ? editCriticality : undefined,
        tags: editTags,
        asset_metadata: metadata,
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["asset", id] });
      qc.invalidateQueries({ queryKey: ["assets"] });
      qc.invalidateQueries({ queryKey: ["asset-tags"] });
      setEditing(false);
    },
  });

  function startEdit() {
    if (!asset) return;
    setEditName(asset.name);
    setEditCriticality(asset.criticality);
    setEditTags([...(asset.tags ?? [])]);
    setEditMetadata(JSON.stringify(asset.asset_metadata, null, 2));
    setMetadataError("");
    setEditing(true);
  }

  function cancelEdit() {
    setEditing(false);
    setMetadataError("");
    setTagInput("");
  }

  const handleDiscoverApplications = async () => {
    setDiscoverLoading(true);
    setDiscoverError(null);
    try {
      await apiClient.post(`/assets/${asset?.id}/discover-applications`);
      qc.invalidateQueries({ queryKey: ["asset", id] });
    } catch (err: any) {
      setDiscoverError(err?.response?.data?.detail || "Discovery failed");
    } finally {
      setDiscoverLoading(false);
    }
  };

  function addTag() {
    const tag = tagInput.trim();
    if (tag && !editTags.includes(tag)) {
      setEditTags([...editTags, tag]);
    }
    setTagInput("");
  }

  function removeTag(tag: string) {
    setEditTags(editTags.filter((t) => t !== tag));
  }

  if (isLoading) return <PageLoading />;
  if (!asset) return <div className="p-8 text-slate-500">Asset not found.</div>;

  return (
    <div className="p-8 max-w-5xl">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <button onClick={() => navigate(-1)}
            className="p-1.5 text-slate-400 hover:text-slate-600 rounded hover:bg-slate-100">
            <ArrowLeft className="w-5 h-5" />
          </button>
          <div>
            <h1 className="text-xl font-semibold text-slate-900">{asset.name}</h1>
            <div className="text-sm text-slate-400">{asset.asset_type.replace(/_/g, " ")} · {asset.environment}</div>
            {asset.connector_name && (
              <div className="flex items-center gap-2 text-sm text-slate-500 mt-1">
                <span className="text-slate-400">Source connector:</span>
                <span className="font-medium text-slate-700">{asset.connector_name}</span>
              </div>
            )}
          </div>
        </div>
        <div className="flex gap-2">
          {!editing ? (
            <button onClick={startEdit}
              className="inline-flex items-center gap-1.5 px-3 py-2 text-sm border border-slate-200 rounded-md hover:bg-slate-50">
              <Edit className="w-4 h-4" /> Edit
            </button>
          ) : (
            <>
              <button onClick={cancelEdit}
                className="inline-flex items-center gap-1.5 px-3 py-2 text-sm border border-slate-200 rounded-md hover:bg-slate-50">
                <X className="w-4 h-4" /> Cancel
              </button>
              <button
                onClick={() => updateMutation.mutate()}
                disabled={updateMutation.isPending || !!metadataError}
                className="inline-flex items-center gap-1.5 px-3 py-2 text-sm bg-brand-600 text-white rounded-md hover:bg-brand-700 disabled:opacity-50">
                <Save className="w-4 h-4" />
                {updateMutation.isPending ? "Saving…" : "Save"}
              </button>
            </>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left: Properties + Metadata */}
        <div className="lg:col-span-2 space-y-5">
          <div className="bg-white border border-slate-200 rounded-lg p-5">
            <h2 className="text-sm font-semibold text-slate-900 mb-4">Properties</h2>
            <dl className="grid grid-cols-2 gap-x-6 gap-y-3">
              <div>
                <dt className="text-xs text-slate-400">Name</dt>
                {editing ? (
                  <input value={editName} onChange={(e) => setEditName(e.target.value)}
                    className="mt-0.5 w-full text-sm border border-slate-200 rounded px-2 py-1 focus:outline-none focus:ring-2 focus:ring-brand-500" />
                ) : (
                  <dd className="text-sm text-slate-900 font-medium mt-0.5">{asset.name}</dd>
                )}
              </div>
              <div>
                <dt className="text-xs text-slate-400">Criticality</dt>
                {editing ? (
                  <select value={editCriticality} onChange={(e) => setEditCriticality(e.target.value as Criticality)}
                    className="mt-0.5 w-full text-sm border border-slate-200 rounded px-2 py-1">
                    {["low", "medium", "high", "critical"].map((c) => <option key={c} value={c}>{c}</option>)}
                  </select>
                ) : (
                  <dd className="mt-0.5"><RiskBadge level={asset.criticality} size="sm" /></dd>
                )}
              </div>
              <div>
                <dt className="text-xs text-slate-400">Type</dt>
                <dd className="text-sm text-slate-900 mt-0.5">{asset.asset_type.replace(/_/g, " ")}</dd>
                {editing && <p className="text-xs text-slate-400 mt-0.5">Cannot be changed after creation.</p>}
              </div>
              <div>
                <dt className="text-xs text-slate-400">Environment</dt>
                <dd className="text-sm text-slate-900 mt-0.5">{asset.environment}</dd>
                {editing && <p className="text-xs text-slate-400 mt-0.5">Cannot be changed after creation.</p>}
              </div>
            </dl>
          </div>

          <div className="bg-white border border-slate-200 rounded-lg p-5">
            <h2 className="text-sm font-semibold text-slate-900 mb-3">Metadata</h2>
            {editing ? (
              <>
                <textarea
                  value={editMetadata}
                  onChange={(e) => { setEditMetadata(e.target.value); setMetadataError(""); }}
                  rows={8}
                  className={`w-full text-xs font-mono border rounded px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-500 ${metadataError ? "border-red-400" : "border-slate-200"}`}
                />
                {metadataError && <p className="text-xs text-red-500 mt-1">{metadataError}</p>}
              </>
            ) : Object.keys(asset.asset_metadata).length === 0 ? (
              <p className="text-sm text-slate-400">No metadata.</p>
            ) : (() => {
              const m = asset.asset_metadata as Record<string, unknown>;
              const row = (label: string, value: unknown) => value != null && value !== "" ? (
                <div key={label}>
                  <dt className="text-xs text-slate-400">{label}</dt>
                  <dd className="text-sm text-slate-900 mt-0.5 font-mono">{String(value)}</dd>
                </div>
              ) : null;
              const badge = (text: string, color = "slate") => (
                <span className={`inline-block px-2 py-0.5 text-xs rounded bg-${color}-100 text-${color}-700`}>{text}</span>
              );

              if (asset.asset_type === "cloud_account") return (
                <dl className="space-y-2">
                  {row("Account ID", m.account_id)}
                  {row("Account Alias", m.account_alias)}
                  {row("Region", m.region)}
                  {row("Provider", m.provider || m.connector_type)}
                  {row("Subscription / Project", m.subscription_id ?? m.project_id)}
                  {row("Tenant / Org", m.tenant_id ?? m.organization_id)}
                </dl>
              );

              if (asset.asset_type === "dns_zone") return (
                <dl className="space-y-2">
                  {row("Zone Name", m.zone_name ?? m.name)}
                  {row("Zone ID", m.zone_id ?? m.hosted_zone_id)}
                  {row("Type", m.private_zone === true ? "Private" : m.private_zone === false ? "Public" : m.type)}
                  {row("Record Count", m.record_count ?? m.resource_record_set_count)}
                  {row("TTL Default", m.default_ttl)}
                </dl>
              );

              if (asset.asset_type === "firewall") return (
                <dl className="space-y-2">
                  {row("Group ID", m.group_id ?? m.security_group_id)}
                  {row("Group Name", m.group_name)}
                  {row("VPC", m.vpc_id)}
                  {row("Inbound Rules", m.inbound_rule_count ?? (Array.isArray(m.inbound_rules) ? (m.inbound_rules as unknown[]).length : undefined))}
                  {row("Outbound Rules", m.outbound_rule_count ?? (Array.isArray(m.outbound_rules) ? (m.outbound_rules as unknown[]).length : undefined))}
                </dl>
              );

              if (asset.asset_type === "identity" || asset.asset_type === "identity_provider") return (
                <dl className="space-y-2">
                  {row("Username / ID", m.username ?? m.user_id ?? m.provider_id)}
                  {row("Email", m.email)}
                  {row("Provider Type", m.provider_type ?? m.type)}
                  {row("Domain", m.domain ?? m.sso_domain)}
                  {m.mfa_enabled != null && (
                    <div key="mfa">
                      <dt className="text-xs text-slate-400">MFA</dt>
                      <dd className="mt-0.5">{badge(m.mfa_enabled ? "Enabled" : "Disabled", m.mfa_enabled ? "green" : "red")}</dd>
                    </div>
                  )}
                  {row("Last Login", m.last_login ?? m.last_sign_in)}
                  {row("SSO URL", m.sso_url)}
                </dl>
              );

              if (asset.asset_type === "database") return (
                <dl className="space-y-2">
                  {row("Engine", m.engine ?? m.db_engine)}
                  {row("Version", m.engine_version ?? m.version)}
                  {row("Endpoint", m.endpoint ?? m.host)}
                  {row("Port", m.port)}
                  {row("Instance Class", m.db_instance_class ?? m.instance_class)}
                  {m.multi_az != null && (
                    <div key="multi_az">
                      <dt className="text-xs text-slate-400">Multi-AZ</dt>
                      <dd className="mt-0.5">{badge(m.multi_az ? "Yes" : "No", m.multi_az ? "green" : "slate")}</dd>
                    </div>
                  )}
                  {row("Status", m.db_instance_status ?? m.status)}
                  {row("Storage (GB)", m.allocated_storage)}
                </dl>
              );

              if (asset.asset_type === "storage_bucket") return (
                <dl className="space-y-2">
                  {row("Bucket Name", m.bucket_name ?? m.name)}
                  {row("Region", m.region ?? m.location)}
                  {row("ARN", m.arn)}
                  {m.versioning_enabled != null && (
                    <div key="ver">
                      <dt className="text-xs text-slate-400">Versioning</dt>
                      <dd className="mt-0.5">{badge(m.versioning_enabled ? "Enabled" : "Disabled", m.versioning_enabled ? "green" : "slate")}</dd>
                    </div>
                  )}
                  {m.public_access_blocked != null && (
                    <div key="pub">
                      <dt className="text-xs text-slate-400">Public Access</dt>
                      <dd className="mt-0.5">{badge(m.public_access_blocked ? "Blocked" : "Open", m.public_access_blocked ? "green" : "red")}</dd>
                    </div>
                  )}
                  {row("Storage Class", m.storage_class)}
                </dl>
              );

              if (asset.asset_type === "load_balancer") return (
                <dl className="space-y-2">
                  {row("DNS Name", m.dns_name)}
                  {row("ARN", m.load_balancer_arn ?? m.arn)}
                  {row("Type", m.load_balancer_type ?? m.type)}
                  {row("Scheme", m.scheme)}
                  {row("VPC", m.vpc_id)}
                  {row("State", m.state_code ?? m.state)}
                  {Array.isArray(m.availability_zones) && (
                    <div key="az">
                      <dt className="text-xs text-slate-400">Availability Zones</dt>
                      <dd className="mt-0.5 text-sm text-slate-700">{(m.availability_zones as string[]).join(", ")}</dd>
                    </div>
                  )}
                </dl>
              );

              if (asset.asset_type === "key_pair") return (
                <dl className="space-y-2">
                  {row("Key Name", m.key_name ?? m.name)}
                  {row("Key ID", m.key_pair_id ?? m.key_id)}
                  {row("Fingerprint", m.key_fingerprint ?? m.fingerprint)}
                  {row("Type", m.key_type)}
                  {row("Region", m.region)}
                </dl>
              );

              if (asset.asset_type === "container_cluster" || asset.asset_type === "kubernetes_cluster") return (
                <dl className="space-y-2">
                  {row("Cluster Name", m.cluster_name ?? m.name)}
                  {row("Version", m.version ?? m.kubernetes_version)}
                  {row("Node Count", m.node_count ?? m.node_group_count)}
                  {row("Endpoint", m.endpoint)}
                  {row("Region", m.region ?? m.location)}
                  {row("Status", m.status)}
                  {row("Platform", m.platform ?? m.cloud_provider)}
                </dl>
              );

              if (asset.asset_type === "kubernetes_workload") return (
                <dl className="space-y-2">
                  {row("Kind", m.kind)}
                  {row("Namespace", m.namespace)}
                  {row("Image", m.image ?? (Array.isArray(m.images) ? (m.images as string[]).join(", ") : undefined))}
                  {row("Replicas", m.replicas ?? m.desired_replicas)}
                  {row("Ready", m.ready_replicas != null ? `${m.ready_replicas} / ${m.replicas ?? "?"}` : undefined)}
                  {row("Cluster", m.cluster_name ?? m.cluster)}
                </dl>
              );

              if (asset.asset_type === "container_image") return (
                <dl className="space-y-2">
                  {row("Repository", m.repository ?? m.repo)}
                  {row("Tag", m.tag)}
                  {row("Digest", m.digest)}
                  {row("Size", m.size_mb != null ? `${m.size_mb} MB` : m.size)}
                  {row("Registry", m.registry)}
                  {row("Pushed At", m.pushed_at ?? m.created_at)}
                </dl>
              );

              if (asset.asset_type === "application") return (
                <dl className="space-y-2">
                  {row("Version", m.version ?? m.app_version)}
                  {row("Deployment Type", m.deployment_type ?? m.type)}
                  {row("Endpoint / URL", m.endpoint ?? m.url)}
                  {row("Namespace", m.namespace)}
                  {row("Chart", m.chart_name ?? m.helm_chart)}
                  {row("Replicas", m.replicas)}
                </dl>
              );

              // Generic fallback for any other type
              return (
                <pre className="text-xs font-mono text-slate-700 bg-slate-50 rounded p-3 overflow-auto">
                  {JSON.stringify(asset.asset_metadata, null, 2)}
                </pre>
              );
            })()}
          </div>

          {/* Network — shown for server and endpoint assets */}
          {(asset.asset_type === "server" || asset.asset_type === "endpoint") && (
            <div className="bg-white border border-slate-200 rounded-lg p-5">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-sm font-semibold text-slate-900 flex items-center gap-2">
                  <Network className="w-4 h-4 text-slate-400" />
                  Network
                </h2>
                <button
                  onClick={() => setShowIPWizard(true)}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-brand-600 text-white rounded-md hover:bg-brand-700"
                >
                  Change IP
                </button>
              </div>
              <dl className="space-y-2">
                {Array.isArray(asset.asset_metadata.ip_addresses) &&
                  (asset.asset_metadata.ip_addresses as string[]).length > 0 ? (
                    <div>
                      <dt className="text-xs text-slate-400">IP Addresses</dt>
                      <dd className="mt-0.5 flex flex-wrap gap-1">
                        {(asset.asset_metadata.ip_addresses as string[]).map((ip) => (
                          <span key={ip} className="font-mono text-xs bg-slate-100 text-slate-700 px-2 py-0.5 rounded">
                            {ip}
                          </span>
                        ))}
                      </dd>
                    </div>
                  ) : asset.asset_metadata.private_ip ? (
                    <div>
                      <dt className="text-xs text-slate-400">IP Address</dt>
                      <dd className="font-mono text-sm text-slate-900 mt-0.5">
                        {asset.asset_metadata.private_ip as string}
                      </dd>
                    </div>
                  ) : (
                    <p className="text-sm text-slate-400">No IP address recorded in metadata.</p>
                  )}
                {Array.isArray(asset.asset_metadata.dns_names) &&
                  (asset.asset_metadata.dns_names as string[]).length > 0 && (
                    <div>
                      <dt className="text-xs text-slate-400">DNS Names</dt>
                      <dd className="mt-0.5 flex flex-wrap gap-1">
                        {(asset.asset_metadata.dns_names as string[]).map((name) => (
                          <span key={name} className="font-mono text-xs bg-blue-50 text-blue-700 border border-blue-100 px-2 py-0.5 rounded">
                            {name}
                          </span>
                        ))}
                      </dd>
                    </div>
                  )}
              </dl>
            </div>
          )}
        </div>

        {/* Right: Tags + Change Requests */}
        <div className="space-y-5">
          <div className="bg-white border border-slate-200 rounded-lg p-5">
            <h2 className="text-sm font-semibold text-slate-900 mb-3">Tags</h2>
            <div className="flex flex-wrap gap-1.5 mb-2">
              {(editing ? editTags : (asset.tags ?? [])).map((t) => (
                <span key={t}
                  className="inline-flex items-center gap-1 px-2 py-0.5 text-xs bg-slate-100 text-slate-700 rounded-full">
                  {t}
                  {editing && (
                    <button onClick={() => removeTag(t)} className="text-slate-400 hover:text-red-500">
                      <X className="w-3 h-3" />
                    </button>
                  )}
                </span>
              ))}
              {!editing && (asset.tags ?? []).length === 0 && (
                <span className="text-xs text-slate-400">No tags. Click Edit to add.</span>
              )}
            </div>
            {editing && (
              <div className="flex gap-1 mt-2">
                <input
                  list="tag-options-detail"
                  value={tagInput}
                  onChange={(e) => setTagInput(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); addTag(); } }}
                  placeholder="Add tag…"
                  className="flex-1 text-sm border border-slate-200 rounded px-2 py-1 focus:outline-none focus:ring-2 focus:ring-brand-500"
                />
                <datalist id="tag-options-detail">
                  {(allTags ?? []).map((t) => <option key={t} value={t} />)}
                </datalist>
                <button onClick={addTag}
                  className="p-1.5 bg-brand-600 text-white rounded hover:bg-brand-700">
                  <Plus className="w-4 h-4" />
                </button>
              </div>
            )}
          </div>

          {(() => {
            const actions = (ASSET_ACTIONS[asset.asset_type] ?? []).filter(
              (action) =>
                (!action.connectorType || action.connectorType === asset.connector_type) &&
                (!action.condition || action.condition(asset))
            );
            return actions.length > 0 && (
            <div className="bg-white border border-slate-200 rounded-lg p-5">
              <h2 className="text-sm font-semibold text-slate-900 mb-3 flex items-center gap-1.5">
                <Zap className="w-4 h-4 text-brand-500" /> Quick Actions
              </h2>
              <div className="space-y-1.5">
                {actions.map((action) => (
                  <button
                    key={action.changeType}
                    onClick={() => {
                      const params = new URLSearchParams({
                        changeType: action.changeType,
                        assetId: asset.id,
                        title: action.title(asset),
                        description: action.description(asset),
                      });
                      navigate(`/change-requests/new?${params.toString()}`);
                    }}
                    className="w-full text-left px-3 py-2 text-sm rounded-md border border-slate-200 hover:border-brand-300 hover:bg-brand-50 text-slate-700 hover:text-brand-800 transition-colors"
                  >
                    {action.label}
                  </button>
                ))}
              </div>
            </div>
          );
          })()}

          <div className="bg-white border border-slate-200 rounded-lg p-5">
            <h2 className="text-sm font-semibold text-slate-900 mb-3">Change Requests</h2>
            {!linkedCRs || linkedCRs.length === 0 ? (
              <p className="text-xs text-slate-400">No change requests targeting this asset.</p>
            ) : (
              <div className="space-y-2">
                {linkedCRs.slice(0, 10).map((cr) => (
                  <button key={cr.id}
                    onClick={() => navigate(`/change-requests/${cr.id}`)}
                    className="w-full text-left p-2 rounded hover:bg-slate-50 border border-transparent hover:border-slate-200">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-medium text-slate-900 truncate">{cr.title}</span>
                      <StatusBadge status={cr.status} size="sm" />
                    </div>
                    <div className="text-xs text-slate-400 mt-0.5">
                      {new Date(cr.created_at).toLocaleDateString()}
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Applications (containerization discovery) */}
          {(asset.asset_type === "server" || asset.asset_type === "endpoint") && (
            <div className="bg-white border border-slate-200 rounded-lg p-5 mt-4">
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-sm font-semibold text-slate-900">Applications</h2>
                <button
                  onClick={handleDiscoverApplications}
                  disabled={discoverLoading}
                  className="text-xs text-brand-600 hover:underline disabled:opacity-50"
                >
                  {discoverLoading ? "Scanning…" : "Re-scan"}
                </button>
              </div>
              {discoverError && (
                <p className="text-xs text-red-500 mb-2">{discoverError}</p>
              )}
              {Array.isArray((asset.asset_metadata as Record<string, unknown>)?.applications) ? (
                <div className="space-y-1.5">
                  {((asset.asset_metadata as Record<string, unknown>).applications as Array<Record<string, unknown>>).map((app, i) => (
                    <div key={String(app.id ?? i)} className="flex items-center justify-between bg-slate-50 border border-slate-100 rounded-md px-3 py-2 text-xs">
                      <div>
                        <span className="font-medium text-slate-900">{String(app.name)}</span>
                        {Array.isArray(app.listening_ports) && (app.listening_ports as Array<Record<string, unknown>>).length > 0 && (
                          <span className="text-slate-400 ml-2">
                            :{(app.listening_ports as Array<Record<string, unknown>>).map(p => String(p.port ?? p)).join(", :")}
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-2">
                        {app.stateful ? (
                          <span className="px-1.5 py-0.5 rounded text-amber-700 bg-amber-50 border border-amber-200">
                            stateful{app.estimated_data_size_gb ? ` · ${Number(app.estimated_data_size_gb).toFixed(1)} GB` : ""}
                          </span>
                        ) : (
                          <span className="px-1.5 py-0.5 rounded text-slate-500 bg-slate-100">
                            stateless
                          </span>
                        )}
                        <span className="text-slate-400">{String(app.containerization_status ?? "not_started")}</span>
                        <button
                          onClick={() => { setContainerizeApp(app); setShowContainerizeWizard(true); }}
                          className="px-2 py-0.5 rounded bg-brand-600 text-white hover:bg-brand-700 text-xs"
                        >
                          Containerize
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-xs text-slate-400 bg-slate-50 rounded-md px-3 py-4 text-center border border-slate-100">
                  No applications discovered — run a scan to detect running services
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* IP Migration Wizard modal */}
      {showIPWizard && (
        <IPMigrationWizard
          assetId={asset.id}
          currentIp={
            Array.isArray(asset.asset_metadata.ip_addresses)
              ? (asset.asset_metadata.ip_addresses as string[])[0]
              : (asset.asset_metadata.private_ip as string | undefined)
          }
          currentGateway={asset.asset_metadata.gateway as string | undefined}
          currentDnsNames={
            Array.isArray(asset.asset_metadata.dns_names)
              ? (asset.asset_metadata.dns_names as string[])
              : []
          }
          onClose={() => setShowIPWizard(false)}
        />
      )}

      {/* Containerization Wizard modal */}
      {showContainerizeWizard && (
        <ContainerizationWizard
          assetId={asset.id}
          preselectedApp={containerizeApp}
          onClose={() => { setShowContainerizeWizard(false); setContainerizeApp(null); }}
        />
      )}
    </div>
  );
}
