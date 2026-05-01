import uuid
import pytest
from app.models.agent import AgentRegistration, AgentJob, OsType, AgentJobStatus


def test_os_type_enum_values():
    assert OsType.linux == "linux"
    assert OsType.windows == "windows"


def test_agent_job_status_enum_values():
    assert AgentJobStatus.pending == "pending"
    assert AgentJobStatus.running == "running"
    assert AgentJobStatus.completed == "completed"
    assert AgentJobStatus.failed == "failed"


def test_connector_type_includes_nexplane_agent():
    from app.models.connector import ConnectorType
    assert ConnectorType.nexplane_agent == "nexplane_agent"
