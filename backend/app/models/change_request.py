import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, DateTime, func, ForeignKey, Enum as SAEnum, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
import enum

from app.database import Base


class ChangeType(str, enum.Enum):
    dns_update = "dns_update"
    snapshot_asset = "snapshot_asset"
    security_group_update = "security_group_update"
    key_rotation = "key_rotation"
    telemetry_agent_deploy = "telemetry_agent_deploy"
    remote_command = "remote_command"
    microsegmentation_policy = "microsegmentation_policy"
    ec2_stop = "ec2_stop"
    ec2_start = "ec2_start"
    ec2_reboot = "ec2_reboot"
    ec2_stop_start = "ec2_stop_start"
    ec2_launch = "ec2_launch"
    ec2_terminate = "ec2_terminate"
    key_pair_create = "key_pair_create"
    ssm_command = "ssm_command"
    tailscale_join = "tailscale_join"
    tailscale_remove = "tailscale_remove"
    deploy_nexplane_agent = "deploy_nexplane_agent"
    terraform_local_apply = "terraform_local_apply"
    ansible_local_playbook = "ansible_local_playbook"
    # AWS expansion — Plan 1
    iam_user_create = "iam_user_create"
    iam_user_delete = "iam_user_delete"
    s3_bucket_create = "s3_bucket_create"
    s3_bucket_delete = "s3_bucket_delete"
    s3_lifecycle_configure = "s3_lifecycle_configure"
    route53_zone_create = "route53_zone_create"
    route53_record_upsert = "route53_record_upsert"
    route53_record_delete = "route53_record_delete"
    rds_instance_create = "rds_instance_create"
    rds_instance_delete = "rds_instance_delete"
    rds_snapshot_create = "rds_snapshot_create"
    cloudwatch_alarm_create = "cloudwatch_alarm_create"
    cloudwatch_alarm_delete = "cloudwatch_alarm_delete"
    # GCP instance lifecycle — Sub-project A
    gce_instance_create = "gce_instance_create"
    gce_stop = "gce_stop"
    gce_start = "gce_start"
    gce_instance_reboot = "gce_instance_reboot"
    gce_instance_delete = "gce_instance_delete"
    gce_disk_snapshot = "gce_disk_snapshot"
    # Azure VM lifecycle — Sub-project A
    azure_vm_create = "azure_vm_create"
    azure_vm_stop = "azure_vm_stop"
    azure_vm_start = "azure_vm_start"
    azure_vm_reboot = "azure_vm_reboot"
    azure_vm_delete = "azure_vm_delete"
    azure_vm_snapshot = "azure_vm_snapshot"
    azure_run_command = "azure_run_command"
    # Patch management
    patch_packages = "patch_packages"
    patch_campaign = "patch_campaign"
    # Vulnerability remediation
    s3_block_public_access = "s3_block_public_access"
    iam_enforce_mfa = "iam_enforce_mfa"
    generic_remediation = "generic_remediation"
    notify_only = "notify_only"
    suppress = "suppress"
    # Identity lifecycle
    offboard_user = "offboard_user"
    onboard_user = "onboard_user"
    # Credential rotation
    rotate_db_credentials = "rotate_db_credentials"
    rotate_ssh_keys = "rotate_ssh_keys"
    rotate_api_key = "rotate_api_key"
    rotate_service_account = "rotate_service_account"
    # Incident response
    isolate_host = "isolate_host"
    lockdown_account = "lockdown_account"
    phishing_response = "phishing_response"
    preserve_evidence = "preserve_evidence"
    # IaC orchestration
    terraform_apply = "terraform_apply"
    ansible_playbook = "ansible_playbook"
    helm_upgrade = "helm_upgrade"
    # Database administration
    provision_db_user = "provision_db_user"
    deprovision_db_user = "deprovision_db_user"
    db_permission_change = "db_permission_change"
    configure_db_audit = "configure_db_audit"
    promote_db_replica = "promote_db_replica"
    db_connection_config = "db_connection_config"
    # Fleet operations
    rolling_restart = "rolling_restart"
    canary_config_push = "canary_config_push"
    distribute_file = "distribute_file"
    fleet_health_check = "fleet_health_check"
    # Backup & recovery
    create_backup = "create_backup"
    verify_backup = "verify_backup"
    restore_files = "restore_files"
    dr_failover = "dr_failover"
    scheduled_reboot = "scheduled_reboot"
    # Compliance
    enforce_cis_benchmark = "enforce_cis_benchmark"
    collect_evidence = "collect_evidence"
    # Coverage phases U and V
    block_s3_public_access = "block_s3_public_access"
    restore_s3_public_access = "restore_s3_public_access"
    capture_instance_state = "capture_instance_state"
    # AWS Plans 2-4 — IAM, S3, Route53, agent, DR failover, RDS replica, GCP/Azure ops
    attach_iam_policy = "attach_iam_policy"
    detach_iam_policy = "detach_iam_policy"
    disable_iam_user = "disable_iam_user"
    enable_iam_user = "enable_iam_user"
    rotate_iam_key = "rotate_iam_key"
    put_bucket_policy = "put_bucket_policy"
    tag_resource = "tag_resource"
    remove_nexplane_agent = "remove_nexplane_agent"
    dr_dns_failover_route53 = "dr_dns_failover_route53"
    rds_replica_create = "rds_replica_create"
    # GCP Plans 3 — firewall, storage, service accounts
    gcp_firewall_create = "gcp_firewall_create"
    gcp_firewall_delete = "gcp_firewall_delete"
    gcp_block_public_bucket_access = "gcp_block_public_bucket_access"
    gcp_disable_service_account = "gcp_disable_service_account"
    gcp_rotate_service_account_key = "gcp_rotate_service_account_key"
    # Azure Plans 4 — NSG, blob storage, storage key
    azure_update_nsg_rule = "azure_update_nsg_rule"
    azure_restore_nsg_rule = "azure_restore_nsg_rule"
    azure_disable_public_blob_access = "azure_disable_public_blob_access"
    azure_enable_public_blob_access = "azure_enable_public_blob_access"
    azure_rotate_storage_key = "azure_rotate_storage_key"
    azure_storage_account_create = "azure_storage_account_create"
    azure_storage_account_delete = "azure_storage_account_delete"
    azure_blob_container_create = "azure_blob_container_create"
    azure_blob_container_delete = "azure_blob_container_delete"
    azure_managed_identity_create = "azure_managed_identity_create"
    azure_managed_identity_delete = "azure_managed_identity_delete"
    azure_role_assignment_create = "azure_role_assignment_create"
    azure_role_assignment_delete = "azure_role_assignment_delete"
    azure_vnet_create = "azure_vnet_create"
    azure_vnet_delete = "azure_vnet_delete"
    azure_dns_zone_create = "azure_dns_zone_create"
    azure_dns_zone_delete = "azure_dns_zone_delete"
    azure_dns_record_create = "azure_dns_record_create"
    azure_dns_record_delete = "azure_dns_record_delete"
    azure_sql_server_create = "azure_sql_server_create"
    azure_sql_server_delete = "azure_sql_server_delete"
    azure_sql_database_create = "azure_sql_database_create"
    azure_sql_database_delete = "azure_sql_database_delete"
    azure_metric_alert_create = "azure_metric_alert_create"
    azure_metric_alert_delete = "azure_metric_alert_delete"
    # Agent command group change types (Plans 4)
    agent_linux_patch = "agent_linux_patch"
    agent_ossecurity = "agent_ossecurity"
    agent_linuxauth = "agent_linuxauth"
    agent_crossplatform = "agent_crossplatform"
    agent_compliance = "agent_compliance"
    agent_forensics = "agent_forensics"
    agent_fleet = "agent_fleet"
    agent_backup = "agent_backup"
    agent_reboot = "agent_reboot"
    agent_credrotation = "agent_credrotation"
    agent_iac = "agent_iac"
    agent_linuxupgrade = "agent_linuxupgrade"
    agent_win_patch = "agent_win_patch"
    agent_winharden = "agent_winharden"
    # ALB lifecycle
    alb_create = "alb_create"
    alb_delete = "alb_delete"
    target_group_create = "target_group_create"
    listener_create = "listener_create"
    listener_modify = "listener_modify"
    target_group_delete = "target_group_delete"
    listener_delete = "listener_delete"
    register_targets = "register_targets"
    deregister_targets = "deregister_targets"
    # Containerize legacy workloads
    agent_appdiscovery = "agent_appdiscovery"
    agent_containerize_build = "agent_containerize_build"
    k8s_workload_deploy = "k8s_workload_deploy"
    agent_containerize_retire = "agent_containerize_retire"
    # IP address migration
    change_ip = "change_ip"
    migrate_ip = "migrate_ip"
    ip_campaign = "ip_campaign"


class RiskLevel(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class ChangeRequestStatus(str, enum.Enum):
    draft = "draft"
    planned = "planned"
    safety_review = "safety_review"
    awaiting_approval = "awaiting_approval"
    approved = "approved"
    executing = "executing"
    verifying = "verifying"
    completed = "completed"
    failed = "failed"
    rolled_back = "rolled_back"
    rejected = "rejected"
    # Fleet operations
    queued_for_maintenance = "queued_for_maintenance"
    preflight_running = "preflight_running"
    preflight_failed = "preflight_failed"
    batch_running = "batch_running"
    batch_aborted = "batch_aborted"
    completed_with_errors = "completed_with_errors"


class ChangeRequest(Base):
    __tablename__ = "change_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    requester_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    change_type: Mapped[ChangeType] = mapped_column(SAEnum(ChangeType, name="change_type"), nullable=False)
    target_asset_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    desired_outcome: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    risk_level: Mapped[RiskLevel] = mapped_column(SAEnum(RiskLevel, name="risk_level"), default=RiskLevel.medium)
    status: Mapped[ChangeRequestStatus] = mapped_column(
        SAEnum(ChangeRequestStatus, name="change_request_status"),
        default=ChangeRequestStatus.draft,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    # Vulnerability remediation fields
    finding_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    source: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    organization: Mapped["Organization"] = relationship("Organization", back_populates="change_requests")
    requester: Mapped["User"] = relationship("User", back_populates="change_requests")
    change_plan: Mapped["ChangePlan"] = relationship("ChangePlan", back_populates="change_request", uselist=False)
    approvals: Mapped[list["Approval"]] = relationship("Approval", back_populates="change_request")
    execution_runs: Mapped[list["ExecutionRun"]] = relationship("ExecutionRun", back_populates="change_request")
    audit_events: Mapped[list["AuditEvent"]] = relationship("AuditEvent", back_populates="change_request")
