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
