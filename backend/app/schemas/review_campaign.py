import uuid
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, field_validator


class CampaignScope(BaseModel):
    connector_ids: Optional[list[str]] = None
    asset_tags: Optional[list[str]] = None
    user_groups: Optional[list[str]] = None
    include_inactive_users: bool = False


class ReviewerAssignmentRule(BaseModel):
    type: Literal["manager_centric", "resource_owner", "security_team"]
    fallback_reviewer_id: Optional[str] = None


class EvidenceOptions(BaseModel):
    include_last_login: bool = True
    include_days_inactive: bool = True
    include_asset_sensitivity: bool = True


class CampaignCreate(BaseModel):
    title: str
    description: Optional[str] = None
    campaign_type: Literal["manager_centric", "resource_owner", "security_team"]
    scope: CampaignScope = CampaignScope()
    reviewer_assignment_rule: ReviewerAssignmentRule
    evidence_options: EvidenceOptions = EvidenceOptions()
    due_date: Optional[datetime] = None


class CampaignOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    organization_id: uuid.UUID
    created_by: uuid.UUID
    title: str
    description: Optional[str]
    campaign_type: str
    scope: dict
    reviewer_assignment_rule: dict
    evidence_options: dict
    status: str
    due_date: Optional[datetime]
    error_message: Optional[str]
    created_at: datetime
    completed_at: Optional[datetime]


class ReviewEntryOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    campaign_id: uuid.UUID
    user_email: str
    user_display_name: Optional[str]
    user_status: str
    resource_name: str
    resource_type: str
    connector_id: Optional[uuid.UUID]
    permission_level: str
    is_privileged: bool
    evidence: dict
    reviewer_id: Optional[uuid.UUID]
    reviewer_unresolved: bool
    decision: Optional[str]
    decision_note: Optional[str]
    decided_at: Optional[datetime]
    decided_by: Optional[uuid.UUID]
    change_request_id: Optional[uuid.UUID]


class EntryDecisionSubmit(BaseModel):
    decision: Literal["keep", "revoke"]
    note: Optional[str] = None


class CampaignApproveOut(BaseModel):
    campaign_id: uuid.UUID
    status: str
    revocations_created: int


class EvidenceExport(BaseModel):
    campaign: CampaignOut
    entries: list[ReviewEntryOut]
    exported_at: datetime
