"""AWS RDS master password rotation and SSM Parameter Store update."""

import boto3
from typing import Any


async def rotate_rds_master_password(
    connector_config: Any,
    rds_instance_id: str,
    new_password: str,
) -> None:
    """
    Updates the master user password for an RDS instance via the AWS API.
    The new password is injected from the step's consumed 'new_password' slot.
    """
    client = boto3.client("rds", **connector_config.boto_kwargs())
    client.modify_db_instance(
        DBInstanceIdentifier=rds_instance_id,
        MasterUserPassword=new_password,
        ApplyImmediately=True,
    )


async def update_ssm_parameter(
    connector_config: Any,
    ssm_path: str,
    new_value: str,
) -> None:
    """Writes new_value to an SSM SecureString parameter at ssm_path."""
    client = boto3.client("ssm", **connector_config.boto_kwargs())
    client.put_parameter(
        Name=ssm_path,
        Value=new_value,
        Type="SecureString",
        Overwrite=True,
    )
