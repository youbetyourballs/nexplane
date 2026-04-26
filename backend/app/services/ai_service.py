import json
import re
from app.services.secrets_service import SecretsService


_SYSTEM_PROMPT_TEMPLATE = """You are a planning assistant for Nexplane, a secure infrastructure change management platform.

The operator is planning a project with this goal: {goal}

Available assets in their environment:
{assets_text}

Your job:
1. Ask targeted clarifying questions ONE AT A TIME to understand scope, affected assets, risk tolerance, and sequencing constraints. Reference available assets by name when relevant.
2. When you have enough information to propose a complete change plan, write your proposal as prose and then append a structured block using this EXACT format:

<nexplane-proposal>
[
  {{
    "title": "Short descriptive title for the change request",
    "change_type": "dns_update|snapshot_asset|security_group_update|key_rotation|telemetry_agent_deploy|remote_command|microsegmentation_policy",
    "suggested_assets": ["asset name 1", "asset name 2"],
    "desired_outcome_sketch": {{}},
    "notes": "Optional sequencing or dependency notes"
  }}
]
</nexplane-proposal>

Only include the <nexplane-proposal> block when you are ready to propose the FULL plan. Do not include it in clarifying question responses."""


class AIService:
    def __init__(self, secrets_service: SecretsService):
        self._secrets = secrets_service

    def _build_system_prompt(self, goal: str, asset_context: list[dict]) -> str:
        if asset_context:
            lines = []
            for a in asset_context:
                line = f"- {a['name']} ({a['asset_type']}, {a['environment']})"
                if a.get("tags"):
                    line += f" [tags: {', '.join(a['tags'])}]"
                lines.append(line)
            assets_text = "\n".join(lines)
        else:
            assets_text = "No assets registered yet."
        return _SYSTEM_PROMPT_TEMPLATE.format(goal=goal, assets_text=assets_text)

    def _parse_proposal(self, text: str) -> list[dict] | None:
        match = re.search(r"<nexplane-proposal>(.*?)</nexplane-proposal>", text, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            return None

    def _strip_proposal_tags(self, text: str) -> str:
        return re.sub(
            r"\s*<nexplane-proposal>.*?</nexplane-proposal>", "", text, flags=re.DOTALL
        ).strip()

    async def chat(
        self,
        api_key: str,
        conversation: list[dict],
        project_goal: str,
        asset_context: list[dict],
    ) -> dict:
        import anthropic
        from app.config import settings

        client = anthropic.AsyncAnthropic(api_key=api_key)
        system_prompt = self._build_system_prompt(project_goal, asset_context)

        messages = [{"role": m["role"], "content": m["content"]} for m in conversation]

        response = await client.messages.create(
            model=settings.AI_MODEL,
            max_tokens=2048,
            system=system_prompt,
            messages=messages,
        )

        reply_text = response.content[0].text
        proposed_crs = self._parse_proposal(reply_text)
        clean_reply = self._strip_proposal_tags(reply_text)

        return {"reply": clean_reply, "proposed_crs": proposed_crs}
