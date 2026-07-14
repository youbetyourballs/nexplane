# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

ROLLBACK_CAPABILITY = "full"

import asyncio
from datetime import datetime, timezone


_DEFAULT_RULES = [
    {
        "name": "expire-objects",
        "action": "DELETE",
        "time_amount": 90,
        "time_unit": "DAYS",
        "is_enabled": True,
    }
]


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    bucket_name = parameters.get("bucket_name", "")
    namespace = parameters.get("namespace", "")
    rules = parameters.get("rules", _DEFAULT_RULES)

    if not creds:
        return {"action": "configure_bucket_lifecycle", "bucket_name": bucket_name, "rules": rules, "mock": True}

    from ._client import get_objectstorage_client
    import oci
    client = get_objectstorage_client(creds)
    loop = asyncio.get_running_loop()

    def _call():
        ns = namespace or client.get_namespace().data
        lifecycle_rules = [
            oci.object_storage.models.ObjectLifecycleRule(
                name=r["name"],
                action=r["action"],
                time_amount=r["time_amount"],
                time_unit=r["time_unit"],
                is_enabled=r.get("is_enabled", True),
            )
            for r in rules
        ]
        policy = oci.object_storage.models.PutObjectLifecyclePolicyDetails(items=lifecycle_rules)
        client.put_object_lifecycle_policy(namespace_name=ns, bucket_name=bucket_name, put_object_lifecycle_policy_details=policy)
        return ns

    ns = await loop.run_in_executor(None, _call)
    return {
        "action": "configure_bucket_lifecycle",
        "bucket_name": bucket_name,
        "namespace": ns,
        "rules_applied": len(rules),
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from ._client import get_objectstorage_client
    import oci
    creds = getattr(connector, "credentials", {})
    bucket_name = execution_result.get("bucket_name", parameters.get("bucket_name", ""))
    namespace = execution_result.get("namespace", parameters.get("namespace", ""))
    if not creds:
        return {"rolled_back": True, "mock": True}
    loop = asyncio.get_running_loop()
    client = get_objectstorage_client(creds)

    def _clear():
        ns = namespace or client.get_namespace().data
        policy = oci.object_storage.models.PutObjectLifecyclePolicyDetails(items=[])
        client.put_object_lifecycle_policy(namespace_name=ns, bucket_name=bucket_name, put_object_lifecycle_policy_details=policy)

    await loop.run_in_executor(None, _clear)
    return {"rolled_back": True, "bucket_name": bucket_name, "rules_cleared": True}
