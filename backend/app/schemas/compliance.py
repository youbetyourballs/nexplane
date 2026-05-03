import uuid
from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel, field_validator


# --- ComplianceBaseline ---

class ComplianceBaselineCreate(BaseModel):
    name: str
    description: Optional[str] = None
    scope_type: Literal["asset", "tag"]
    scope_value: str
    cis_level: Literal[1, 2]
    os_family: Literal["rhel", "debian", "ubuntu"]
    config: dict
    auto_execute: bool = False


class ComplianceBaselineUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    config: Optional[dict] = None
    auto_execute: Optional[bool] = None


class ComplianceBaselineRead(BaseModel):
    id: uuid.UUID
    name: str
    description: Optional[str]
    organization_id: uuid.UUID
    scope_type: str
    scope_value: str
    cis_level: int
    os_family: str
    config: dict
    history: list
    version: int
    auto_execute: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# --- ChangeFreezeWindow ---

class ChangeFreezeWindowCreate(BaseModel):
    reason: str
    start_at: datetime
    end_at: datetime
    emergency_bypass_role: str = "emergency_bypass"

    @field_validator("end_at")
    @classmethod
    def end_after_start(cls, v, info):
        if "start_at" in info.data and v <= info.data["start_at"]:
            raise ValueError("end_at must be after start_at")
        return v


class ChangeFreezeWindowRead(BaseModel):
    id: uuid.UUID
    reason: str
    start_at: datetime
    end_at: datetime
    emergency_bypass_role: str
    created_by: uuid.UUID
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Evidence collection ---

class EvidenceCollectionRequest(BaseModel):
    framework: Literal["soc2", "pci", "iso27001"]
    control_id: str
    asset_ids: list[uuid.UUID]
    evidence_types: list[str] = ["config_files", "command_outputs", "change_logs"]
