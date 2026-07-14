# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import json
import logging
from dataclasses import dataclass, field
from app.services.secrets_service import SecretsService

logger = logging.getLogger(__name__)

_TRIAGE_SYSTEM = """\
You are a Nexplane infrastructure triage assistant. Given a list of scan hits (references to a resource being migrated) and migration context, classify each hit into one of two buckets:

1. confident_updates: hits where you are >80% certain the reference should be updated to the new value. Populate the old_value, new_value, and change_type_params fields.
2. exceptions: hits that are ambiguous, risky, or require operator review. Provide a reason and suggested_action.

Rules:
- Never include secret values in your response. References to secret names/ARNs/paths are fine; actual secret values are not.
- A hit with a known asset_id and a clear string substitution is confident.
- A hit with no asset_id (tier 4) is usually an exception unless the substitution is completely unambiguous.
- Respond ONLY with valid JSON matching the schema: {"confident_updates": [...], "exceptions": [...]}

confident_update schema: {"hit_index": int, "asset_id": "uuid or null", "location": "str", "surface": "str", "old_value": "str", "new_value": "str", "change_type_params": {}}
exception schema: {"hit_index": int, "asset_id": "uuid or null", "reason": "str", "suggested_action": "str", "confidence": float}
"""


@dataclass
class TriageResult:
    confident_updates: list[dict] = field(default_factory=list)
    exceptions: list[dict] = field(default_factory=list)


async def triage_scan_hits(
    hits: list[dict],
    migration_context: dict,
    settings,
    secrets_svc: SecretsService,
) -> TriageResult:
    if not hits:
        return TriageResult()

    import anthropic
    api_key = secrets_svc.decrypt(settings.anthropic_api_key_encrypted)
    client = anthropic.AsyncAnthropic(api_key=api_key)

    user_content = json.dumps({
        "migration_context": migration_context,
        "hits": [
            {
                "index": i,
                "surface": h["hit"]["surface"],
                "location": h["hit"]["location"],
                "matched_term": h["hit"]["matched_term"],
                "snippet": h["hit"]["snippet"],
                "asset_id": str(h["asset_id"]) if h["asset_id"] else None,
                "resolution_tier": h["tier"],
                "resolution_confidence": h["confidence"],
            }
            for i, h in enumerate(hits)
        ],
    }, indent=2)

    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        system=_TRIAGE_SYSTEM,
        messages=[{"role": "user", "content": user_content}],
    )

    try:
        raw = response.content[0].text.strip()
        data = json.loads(raw)
        return TriageResult(
            confident_updates=data.get("confident_updates", []),
            exceptions=data.get("exceptions", []),
        )
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        logger.warning("AI triage response parse failed: %s", e)
        # Fall all hits to exceptions
        return TriageResult(
            exceptions=[
                {
                    "hit_index": i,
                    "asset_id": None,
                    "reason": "AI triage failed to parse",
                    "suggested_action": "manual review",
                    "confidence": 0.0,
                }
                for i in range(len(hits))
            ]
        )
