// Centralized API types — mirrors backend Pydantic schemas
// In production: generate these from OpenAPI via `openapi-typescript`

export type UserRole = "admin" | "security_operator" | "approver" | "auditor";

export interface User {
  id: string;
  organization_id: string;
  email: string;
  name: string;
  role: UserRole;
  created_at: string;
}

export interface Organization {
  id: string;
  name: string;
  created_at: string;
}

export type AssetType =
  | "server"
  | "cloud_account"
  | "dns_zone"
  | "firewall"
  | "identity_provider"
  | "application"
  | "identity"
  | "database"
  | "storage_bucket"
  | "load_balancer"
  | "endpoint"
  | "container_cluster"
  | "key_pair";

export type Environment = "dev" | "staging" | "prod";
export type Criticality = "low" | "medium" | "high" | "critical";

export interface Asset {
  id: string;
  organization_id: string;
  connector_id?: string;
  connector_name?: string;
  connector_type?: string;
  name: string;
  asset_type: AssetType;
  environment: Environment;
  criticality: Criticality;
  asset_metadata: Record<string, unknown>;
  tags: string[];
  created_at: string;
}

export interface AssetCreate {
  name: string;
  asset_type: AssetType;
  environment: Environment;
  criticality: Criticality;
  asset_metadata?: Record<string, unknown>;
  tags?: string[];
}

export interface AssetUpdate {
  name?: string;
  criticality?: Criticality;
  asset_metadata?: Record<string, unknown>;
  tags?: string[];
}

export interface AssetListParams {
  q?: string;
  env?: string;
  asset_type?: string;
  criticality?: string;
  tag?: string;
  connector_id?: string;
}

export type BulkTagOperation = "add" | "remove" | "set";

export interface BulkTagBody {
  asset_ids: string[];
  operation: BulkTagOperation;
  tags: string[];
}

export type ProjectStatus = "draft" | "in_progress" | "completed" | "cancelled";

export interface Project {
  id: string;
  organization_id: string;
  created_by: string;
  name: string;
  description: string;
  goal: string;
  status: ProjectStatus;
  created_at: string;
  updated_at: string;
}

export interface ProjectSummary extends Project {
  member_count: number;
  completed_count: number;
}

export interface ProjectMember {
  id: string;
  project_id: string;
  change_request_id: string;
  sequence_order: number;
  depends_on: string[];
  change_request: ChangeRequestSummary;
  eligible: boolean;
}

export interface ProjectDetail extends Project {
  members: ProjectMember[];
  ai_context: Array<{ role: string; content: string }>;
}

export interface ProjectCreate {
  name: string;
  description?: string;
  goal?: string;
}

export interface ProjectUpdate {
  name?: string;
  description?: string;
  goal?: string;
  status?: ProjectStatus;
}

export interface ProjectMemberCreate {
  change_request_id: string;
  sequence_order?: number;
  depends_on?: string[];
}

export interface ProjectMemberUpdate {
  sequence_order?: number;
  depends_on?: string[];
}

export interface OrgSettings {
  ai_configured: boolean;
  agent_configured: boolean;
  updated_at: string | null;
  agent_secret_plaintext?: string | null;
}

export interface AIProposedCR {
  title: string;
  change_type: ChangeType;
  suggested_assets: string[];
  desired_outcome_sketch: Record<string, unknown>;
  notes?: string;
}

export interface AIChatResponse {
  reply: string;
  proposed_crs: AIProposedCR[] | null;
}

export interface AIChatRequest {
  message: string;
}

export type ConnectorType =
  | "aws"
  | "azure"
  | "cloudflare"
  | "okta"
  | "paloalto"
  | "ssh"
  | "active_directory"
  | "crowdstrike"
  | "tenable"
  | "nexplane_agent"
  | "gcp"
  | "runzero"
  | "wiz"
  | "entra_id"
  | "sentinelone"
  | "defender_endpoint"
  | "hashicorp_vault"
  | "github"
  | "kubernetes"
  | "snyk"
  | "qualys"
  | "terraform"
  | "ansible"
  | "cloudformation"
  | "pulumi"
  | "helm"
  | "bicep"
  | "checkov"
  | "saltstack"
  | "chef_inspec"
  | "jira"
  | "pagerduty"
  | "servicenow"
  | "splunk"
  | "datadog"
  | "zscaler"
  | "google_workspace"
  | "tailscale"
  | "terraform_local"
  | "ansible_local";

export type ConnectorStatus = "active" | "inactive" | "error";

export interface Connector {
  id: string;
  organization_id: string;
  connector_type: ConnectorType;
  name: string;
  status: ConnectorStatus;
  scoped_permissions: Record<string, unknown>;
  created_at: string;
}

export type ConnectorRead = Connector;

export interface ConnectorCreate {
  connector_type: ConnectorType;
  name: string;
  scoped_permissions?: Record<string, unknown>;
}

export interface ConnectorTestResult {
  success: boolean;
  latency_ms: number;
  message: string;
  details: Record<string, unknown>;
}

export interface IngestResponse {
  created: number;
  updated: number;
  assets: Asset[];
}

export type ChangeType =
  | "dns_update"
  | "snapshot_asset"
  | "security_group_update"
  | "key_rotation"
  | "telemetry_agent_deploy"
  | "remote_command"
  | "microsegmentation_policy"
  | "ec2_stop"
  | "ec2_start"
  | "ec2_reboot"
  | "ec2_stop_start"
  | "ec2_launch"
  | "ec2_terminate"
  | "rolling_restart"
  | "canary_config_push"
  | "distribute_file"
  | "fleet_health_check"
  | "key_pair_create"
  | "ssm_command"
  | "tailscale_join"
  | "tailscale_remove"
  | "deploy_nexplane_agent"
  | "terraform_local_apply"
  | "ansible_local_playbook"
  | "iam_user_create"
  | "iam_user_delete"
  | "s3_bucket_create"
  | "s3_bucket_delete"
  | "s3_lifecycle_configure"
  | "route53_zone_create"
  | "route53_record_upsert"
  | "route53_record_delete"
  | "rds_instance_create"
  | "rds_instance_delete"
  | "rds_snapshot_create"
  | "cloudwatch_alarm_create"
  | "cloudwatch_alarm_delete"
  | "alb_create"
  | "alb_delete"
  | "target_group_create"
  | "target_group_delete"
  | "register_targets"
  | "deregister_targets"
  | "listener_create"
  | "listener_modify"
  | "listener_delete"
  | "gce_instance_create"
  | "gce_stop"
  | "gce_start"
  | "gce_instance_reboot"
  | "gce_instance_delete"
  | "gce_disk_snapshot"
  | "azure_vm_create"
  | "azure_vm_stop"
  | "azure_vm_start"
  | "azure_vm_reboot"
  | "azure_vm_delete"
  | "azure_vm_snapshot"
  | "azure_run_command";

export type RiskLevel = "low" | "medium" | "high" | "critical";

export type ChangeRequestStatus =
  | "draft"
  | "planned"
  | "safety_review"
  | "awaiting_approval"
  | "approved"
  | "executing"
  | "verifying"
  | "completed"
  | "failed"
  | "rolled_back"
  | "rejected"
  | "queued_for_maintenance"
  | "preflight_running"
  | "preflight_failed"
  | "batch_running"
  | "batch_aborted"
  | "completed_with_errors";

export interface ChangeRequest {
  id: string;
  organization_id: string;
  requester_id: string;
  title: string;
  description: string;
  change_type: ChangeType;
  target_asset_ids: string[];
  desired_outcome: Record<string, unknown>;
  risk_level: RiskLevel;
  status: ChangeRequestStatus;
  created_at: string;
  updated_at: string;
  requester: User;
  change_plan: ChangePlan | null;
  approvals: Approval[];
  execution_runs: ExecutionRun[];
}

export interface ChangeRequestSummary {
  id: string;
  title: string;
  change_type: ChangeType;
  risk_level: RiskLevel;
  status: ChangeRequestStatus;
  created_at: string;
  updated_at: string;
  requester: User;
}

export interface ChangeRequestCreate {
  title: string;
  description?: string;
  change_type: ChangeType;
  target_asset_ids: string[];
  desired_outcome: Record<string, unknown>;
}

export interface ChangePlanStep {
  step_number: number;
  name: string;
  description: string;
  connector_action: string;
  parameters: Record<string, unknown>;
  rollback_action?: string;
  rollback_parameters?: Record<string, unknown>;
  estimated_duration_seconds: number;
}

export interface PreflightCheck {
  name: string;
  description: string;
  check_type: string;
  expected_result: string;
}

export interface BlastRadius {
  affected_assets: Array<{ id: string; name: string; type: string; env: string; criticality: string }>;
  affected_environments: string[];
  estimated_impact: string;
  affected_services: string[];
  recovery_time_estimate: string;
  rollback_available: boolean;
}

export interface ChangePlan {
  id: string;
  change_request_id: string;
  generated_steps: ChangePlanStep[];
  preflight_checks: PreflightCheck[];
  blast_radius: BlastRadius;
  rollback_plan: Record<string, unknown>;
  verification_plan: { checks: Array<{ name: string; description: string; method: string }>; success_criteria: string };
  generated_by: "system" | "ai_mock" | "human";
  created_at: string;
}

export type ApprovalDecision = "approved" | "rejected";

export interface Approval {
  id: string;
  change_request_id: string;
  approver_id: string;
  decision: ApprovalDecision;
  comment: string | null;
  created_at: string;
  approver: User;
}

export interface ApprovalCreate {
  decision: ApprovalDecision;
  comment?: string;
}

export type ExecutionStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "rolling_back"
  | "rolled_back";

export interface ExecutionRun {
  id: string;
  change_request_id: string;
  workflow_id: string;
  status: ExecutionStatus;
  started_at: string;
  completed_at: string | null;
  result: Record<string, unknown>;
}

export interface AuditEvent {
  id: string;
  organization_id: string;
  actor_id: string | null;
  change_request_id: string | null;
  event_type: string;
  event_payload: Record<string, unknown>;
  created_at: string;
}

export interface Token {
  access_token: string;
  token_type: string;
}

export interface CredentialField {
  name: string;
  label: string;
  type: 'string' | 'password';
  required: boolean;
  default?: string;
}

export interface CredentialStatus {
  configured: boolean;
  fields: CredentialField[];
  updated_at: string | null;
}

export interface AIProviderInfo {
  configured: boolean;
  model?: string;
}

export interface AIProviders {
  default: string | null;
  providers: Record<string, AIProviderInfo>;
}
