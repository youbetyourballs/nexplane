# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import time
from datetime import datetime, timezone


async def _real_execute(creds: dict, parameters: dict) -> dict:
    from ._client import get_boto3_client
    ssm = get_boto3_client(creds, 'ssm')
    loop = asyncio.get_event_loop()
    instance_id = parameters['instance_id']
    document_name = parameters['document_name']
    doc_params = parameters.get('parameters', {})
    # Support a bare `command` string — map it to the SSM document's Commands parameter.
    if not doc_params and 'command' in parameters:
        doc_params = {'commands': [parameters['command']]}

    def _call():
        resp = ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName=document_name,
            Parameters=doc_params or {},
        )
        command_id = resp['Command']['CommandId']
        # Poll for completion (max 5 min)
        for _ in range(60):
            time.sleep(5)
            result = ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
            if result['Status'] not in ('Pending', 'InProgress', 'Delayed'):
                return {"command_id": command_id, "status": result['Status'], "stdout": result.get('StandardOutputContent', ''), "stderr": result.get('StandardErrorContent', '')}
        return {"command_id": command_id, "status": "timeout"}

    result = await loop.run_in_executor(None, _call)
    if result.get("status") not in ("Success", "timeout"):
        stdout = result.get("stdout", "")
        stderr = result.get("stderr", "")
        raise RuntimeError(
            f"SSM command failed ({result.get('status')}): {stderr or stdout or 'no output'}"
        )
    return {"action": "run_ssm_command", "instance_id": instance_id, "document_name": document_name, "executed_at": datetime.now(timezone.utc).isoformat(), **result}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return {"action": "run_ssm_command", "instance_id": parameters.get('instance_id'), "document_name": parameters.get('document_name'), "status": "Success", "stdout": "mock output", "mock": True}
    return await _real_execute(creds, parameters)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "SSM command output is terminal — no automatic rollback"}
