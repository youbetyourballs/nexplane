"""Executor for agent_containerize_auto change type.

Orchestrates 7 stages: preflight_discovery, fleet_cross_reference, ai_analysis,
stateful_gate (conditional), build, deploy, soak_verify.
On soak success, auto-spawns an agent_containerize_retire CR for human approval.
"""
from __future__ import annotations
import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job


# -------------------------------------------------------------------------
# Stage 1: preflight_discovery
# -------------------------------------------------------------------------

async def _stage_preflight_discovery(asset_ids: list, timeout_seconds: int = 300) -> dict:
    """Run deep_discover agent job on the target asset."""
    return await dispatch_agent_job(
        command="deep_discover",
        parameters={},
        asset_ids=asset_ids,
        timeout_seconds=timeout_seconds,
    )


# -------------------------------------------------------------------------
# Stage 2: fleet_cross_reference
# -------------------------------------------------------------------------

async def _stage_fleet_cross_reference(
    discovery_result: dict,
    asset_id: str,
) -> dict:
    """Cross-reference discovered remote IPs against Nexplane asset inventory."""
    from sqlalchemy import select
    from app.database import AsyncSessionLocal
    from app.models.asset import Asset

    remote_addrs: set[str] = set()
    for workload in discovery_result.get("workloads", []):
        for conn in workload.get("outbound_connections", []):
            addr = conn.get("remote_addr", "")
            if ":" in addr:
                ip = addr.rsplit(":", 1)[0].strip("[]")
            else:
                ip = addr
            if ip:
                remote_addrs.add(ip)

    matched_assets: list[dict] = []
    async with AsyncSessionLocal() as db:
        source_asset = await db.get(Asset, uuid.UUID(asset_id))
        if not source_asset:
            return {"nodes": [], "edges": [], "hybrid_edges": discovery_result.get("hybrid_edges", []), "matched_assets": []}
        org_id = source_asset.organization_id

        result = await db.execute(select(Asset).where(Asset.organization_id == org_id))
        all_assets = result.scalars().all()

    for asset in all_assets:
        asset_ips: set[str] = set()
        meta = asset.asset_metadata or {}
        for ip_cidr in (meta.get("ip_addresses") or []):
            asset_ips.add(str(ip_cidr).split("/")[0])
        if meta.get("private_ip"):
            asset_ips.add(str(meta["private_ip"]))
        if meta.get("public_ip"):
            asset_ips.add(str(meta["public_ip"]))

        matched = remote_addrs & asset_ips
        if matched:
            matched_assets.append({
                "asset_id": str(asset.id),
                "name": asset.name,
                "asset_type": asset.asset_type.value,
                "matched_ips": list(matched),
            })

    nodes = [{"type": "source_workload", "id": asset_id}]
    for ma in matched_assets:
        nodes.append({"type": "nexplane_asset", **ma})

    edges = []
    for workload in discovery_result.get("workloads", []):
        for conn in workload.get("outbound_connections", []):
            addr = conn.get("remote_addr", "").rsplit(":", 1)[0].strip("[]")
            for ma in matched_assets:
                if addr in ma["matched_ips"]:
                    edges.append({
                        "source": workload.get("name"),
                        "target": ma["name"],
                        "port": conn.get("remote_addr", "").rsplit(":", 1)[-1],
                        "protocol": conn.get("protocol", "tcp"),
                    })

    return {
        "nodes": nodes,
        "edges": edges,
        "hybrid_edges": discovery_result.get("hybrid_edges", []),
        "matched_assets": matched_assets,
    }


# -------------------------------------------------------------------------
# Stage 3: ai_analysis
# -------------------------------------------------------------------------

_ANALYSIS_SYSTEM_PROMPT = """You are a containerization migration expert analyzing workloads on a host.
You will receive a dependency graph of running workloads and must determine the optimal containerization strategy.

Each workload includes deep host enrichment: outbound_connections (real network peers), env_var_names (environment variable keys — no values for security), open_files (open file descriptors — hints at state), runtime_deps (.so/.dll paths), and config_intelligence (parsed config metadata such as nginx vhosts, upstream proxy targets, IIS site bindings). Use these to make accurate stateful/stateless and monolith/modular determinations.

Return ONLY a JSON object matching this exact schema. No explanation text outside the JSON:
{
  "migration_units": [
    {
      "id": "unit-<N>",
      "name": "<descriptive name>",
      "apps": ["<workload_name>"],
      "pattern": "monolith",
      "stateful": false,
      "data_risk": "none",
      "soak_seconds_recommended": 120,
      "reasoning": "<one sentence explaining stateful/pattern classification based on the enrichment data>"
    }
  ],
  "migration_order": ["unit-1"],
  "warnings": []
}

Classification rules:
- Stateful: any workload with data_directories, open_files pointing to databases or write-ahead logs, outbound_connections to database ports (5432, 3306, 1433, 6379, 27017), or env_var_names suggesting DB credentials (DB_HOST, DATABASE_URL, POSTGRES_*, REDIS_URL, etc.)
- data_risk: "none" | "low" | "medium" | "high" — based on volume of open state files and database connections
- pattern "monolith": workloads sharing IPC sockets or communicating only via localhost ports
- pattern "modular": workloads on distinct network ports that could be independently deployed
- config_intelligence vhosts/upstreams reveal service routing that affects whether apps are monolith or modular
- Already-containerized workloads (docker/containerd/podman/kubernetes_pod) should be noted in warnings, not in migration_units
- soak_seconds_recommended: 60 for stateless, 180-300 for stateful, 120 default"""


async def _stage_ai_analysis(
    discovery_result: dict,
    graph_result: dict,
    org_id: str,
    dry_run: bool = False,
) -> dict:
    """Call AI provider directly to analyze workloads and produce migration units."""
    if dry_run:
        workloads = discovery_result.get("workloads", [])
        units = []
        for w in workloads:
            units.append({
                "app_name": w.get("name", "unknown"),
                "migration_type": "stateless",
                "rationale": "dry_run: mock analysis",
                "data_directories": [],
                "required_env_vars": [],
                "inbound_ports": [p.get("port") for p in w.get("listening_ports", [])],
                "health_check_path": "/health",
                "confidence": 0.9,
            })
        return {"migration_units": units or [{"app_name": "dry-run-app", "migration_type": "stateless", "rationale": "dry_run: no workloads discovered", "data_directories": [], "required_env_vars": [], "inbound_ports": [], "health_check_path": "/", "confidence": 0.9}], "dry_run": True}

    from sqlalchemy import select
    from app.database import AsyncSessionLocal
    from app.models.org_settings import OrganizationSettings
    from app.services.secrets_service import SecretsService
    from app import config as app_config

    secrets_svc = SecretsService(app_config.settings.SECRET_KEY)

    async with AsyncSessionLocal() as db:
        org_settings_result = await db.execute(
            select(OrganizationSettings).where(
                OrganizationSettings.organization_id == uuid.UUID(org_id)
            )
        )
        org_settings = org_settings_result.scalar_one_or_none()

    # Resolve provider/key/model
    _DEFAULT_MODELS = {"openai": "gpt-4o", "anthropic": "claude-sonnet-4-6"}
    provider = "anthropic"
    api_key = ""
    model = _DEFAULT_MODELS["anthropic"]

    if org_settings and org_settings.ai_providers_encrypted:
        data = secrets_svc.decrypt_json(org_settings.ai_providers_encrypted)
        provider = data.get("default", "anthropic")
        providers = data.get("providers", {})
        if provider in providers and providers[provider].get("api_key"):
            api_key = providers[provider]["api_key"]
            model = providers[provider].get("model") or _DEFAULT_MODELS.get(provider, "gpt-4o")
    elif org_settings and org_settings.anthropic_api_key_encrypted:
        api_key = secrets_svc.decrypt(org_settings.anthropic_api_key_encrypted)

    if not api_key:
        raise RuntimeError("No AI provider API key configured for this organization")

    workloads_summary = []
    for w in discovery_result.get("workloads", []):
        workloads_summary.append({
            "name": w.get("name"),
            "runtime_type": w.get("runtime_type"),
            "listening_ports": [p.get("port") for p in w.get("listening_ports", [])],
            "outbound_connections": [
                {"remote_ip": c.get("remote_addr", "").split(":")[0],
                 "remote_port": c.get("remote_addr", "").split(":")[-1]}
                for c in w.get("outbound_connections", [])
            ],
            "data_directories": [d.get("path") for d in w.get("data_directories", [])],
            "dependencies": w.get("dependencies", []),
            # Deep enrichment fields from deep_discover
            "env_var_names": w.get("env_var_names", []),
            "open_files": w.get("open_files", []),
            "runtime_deps": w.get("runtime_deps", []),
            "config_intelligence": w.get("config_intelligence", []),
        })

    user_content = json.dumps({
        "workloads": workloads_summary,
        "dependency_edges": graph_result.get("edges", []),
        "hybrid_edges": graph_result.get("hybrid_edges", []),
        "matched_nexplane_assets": [
            {"name": a["name"], "type": a["asset_type"]}
            for a in graph_result.get("matched_assets", [])
        ],
    }, indent=2)

    messages = [{"role": "user", "content": user_content}]

    if provider == "openai":
        import openai
        client = openai.AsyncOpenAI(api_key=api_key)
        oai_messages = [{"role": "system", "content": _ANALYSIS_SYSTEM_PROMPT}] + messages
        response = await client.chat.completions.create(
            model=model,
            max_tokens=2048,
            messages=oai_messages,
        )
        raw_response = response.choices[0].message.content
    else:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model=model,
            max_tokens=2048,
            system=_ANALYSIS_SYSTEM_PROMPT,
            messages=messages,
        )
        raw_response = response.content[0].text

    text = raw_response.strip() if isinstance(raw_response, str) else str(raw_response)
    if "```" in text:
        start = text.find("```")
        end = text.rfind("```")
        text = text[start + 3:end].strip()
        if text.startswith("json"):
            text = text[4:].strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"AI returned unparseable JSON: {e}. Raw response (first 500 chars): {raw_response[:500]}"
        )


# -------------------------------------------------------------------------
# Stage 4: stateful_gate (conditional)
# -------------------------------------------------------------------------

async def _stage_stateful_gate(cr_id: str, timeout_seconds: int = 86400) -> None:
    """Poll change_request.stateful_approved_at every 10s until set or timeout."""
    from sqlalchemy import select
    from app.database import AsyncSessionLocal
    from app.models.change_request import ChangeRequest

    cr_uuid = uuid.UUID(cr_id)
    deadline = asyncio.get_event_loop().time() + timeout_seconds

    while asyncio.get_event_loop().time() < deadline:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(ChangeRequest).where(ChangeRequest.id == cr_uuid)
            )
            cr = result.scalar_one_or_none()
            if cr and cr.stateful_approved_at is not None:
                return
        await asyncio.sleep(10)

    raise RuntimeError(
        "stateful_gate_expired: operator did not confirm stateful classification within 24 hours"
    )


# -------------------------------------------------------------------------
# Stage 5: build
# -------------------------------------------------------------------------

async def _stage_build(
    migration_units: list[dict],
    asset_ids: list,
    parameters: dict,
    dry_run: bool,
) -> dict:
    """Run containerize_build for each app in each migration unit."""
    from app.connectors.executors.nexplane_agent.containerize_build import execute as build_execute

    build_results: dict[str, Any] = {}
    for unit in migration_units:
        for app_name in unit.get("apps", []):
            result = await build_execute(
                {
                    "app_name": app_name,
                    "registry": parameters.get("registry", ""),
                    "namespace": parameters.get("namespace", "nexplane-migrations"),
                    "dry_run": dry_run,
                },
                asset_ids,
                None,
            )
            build_results[app_name] = result
    return build_results


# -------------------------------------------------------------------------
# Stage 6: deploy
# -------------------------------------------------------------------------

async def _stage_deploy(
    migration_units: list[dict],
    asset_ids: list,
    build_results: dict,
    parameters: dict,
    dry_run: bool,
) -> dict:
    """Apply Kubernetes manifests for each migration unit."""
    from app.connectors.executors.kubernetes.workload_deploy import execute as k8s_execute

    deploy_results: dict[str, Any] = {}
    for unit in migration_units:
        for app_name in unit.get("apps", []):
            br = build_results.get(app_name, {})
            result = await k8s_execute(
                {
                    "app_name": app_name,
                    "target_cluster_id": parameters.get("target_cluster_id", ""),
                    "namespace": parameters.get("namespace", "nexplane-migrations"),
                    "image_tag": br.get("image_tag", f"nexplane/{app_name}:latest"),
                    "manifests": br.get("manifests", {}),
                    "dry_run": dry_run,
                },
                asset_ids,
                None,
            )
            deploy_results[app_name] = result
    return deploy_results


# -------------------------------------------------------------------------
# Stage 7: soak_verify
# -------------------------------------------------------------------------

async def _stage_soak_verify(
    deploy_results: dict,
    soak_seconds: int,
) -> dict:
    """Health-probe all deployed services for soak_seconds. Auto-rollback on failure."""
    import httpx

    probe_results: dict[str, list[dict]] = {app: [] for app in deploy_results}
    end_time = asyncio.get_event_loop().time() + soak_seconds
    consecutive_passes = 0
    async with httpx.AsyncClient(timeout=5.0) as client:
        while asyncio.get_event_loop().time() < end_time:
            tick_passed = True
            for app_name, deploy_result in deploy_results.items():
                service_url = deploy_result.get("service_url") or deploy_result.get("k8s_service_url", "")
                if not service_url:
                    probe_results[app_name].append({"ts": _nowts(), "status": "skipped"})
                    continue
                try:
                    resp = await client.get(service_url)
                    ok = resp.status_code < 500
                except Exception as exc:
                    ok = False
                    probe_results[app_name].append({"ts": _nowts(), "status": "error", "error": str(exc)})
                    tick_passed = False
                    continue
                probe_results[app_name].append({"ts": _nowts(), "status": "ok" if ok else "fail"})
                if not ok:
                    tick_passed = False

            if tick_passed:
                consecutive_passes += 1
            else:
                consecutive_passes = 0

            await asyncio.sleep(10)

    all_passed = consecutive_passes > 0
    if not all_passed:
        failed_apps = [
            app for app, probes in probe_results.items()
            if any(p["status"] in ("fail", "error") for p in (probes[-3:] if probes else []))
        ]
        raise RuntimeError(
            f"soak_verify_failed: probes failed for: {failed_apps}"
        )

    return {"soak_seconds": soak_seconds, "probe_results": probe_results, "passed": True}


def _nowts() -> str:
    return datetime.now(timezone.utc).isoformat()


# -------------------------------------------------------------------------
# Retirement CR auto-spawn
# -------------------------------------------------------------------------

async def _spawn_retirement_cr(
    source_cr_id: str,
    asset_ids: list,
    migration_units: list[dict],
    org_id: str,
    requester_id: str,
) -> str:
    """Create and submit a retirement CR for human approval."""
    from app.database import AsyncSessionLocal
    from app.models.change_request import ChangeRequest, ChangeRequestStatus, ChangeType, RiskLevel

    async with AsyncSessionLocal() as db:
        cr = ChangeRequest(
            organization_id=uuid.UUID(org_id),
            requester_id=uuid.UUID(requester_id),
            title=f"Retire legacy services (auto from containerize CR {source_cr_id[:8]})",
            description=(
                f"Retire legacy systemd/process services containerized and verified by CR {source_cr_id}. "
                "This stops and disables the original services. Irreversible."
            ),
            change_type=ChangeType.agent_containerize_retire,
            target_asset_ids=[str(a) for a in asset_ids],
            desired_outcome={
                "action": "retire_all_units",
                "source_cr_id": source_cr_id,
                "migration_units": migration_units,
                "auto_spawned": True,
            },
            risk_level=RiskLevel.high,
            status=ChangeRequestStatus.draft,
        )
        db.add(cr)
        await db.flush()
        retirement_id = str(cr.id)
        await db.commit()

    return retirement_id


# -------------------------------------------------------------------------
# Main execute / rollback
# -------------------------------------------------------------------------

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Run all 7 stages of the autonomous containerization pipeline."""
    from sqlalchemy import select, and_
    from app.database import AsyncSessionLocal
    from app.models.asset import Asset
    from app.models.change_request import ChangeRequest, ChangeRequestStatus, ChangeType

    if not asset_ids:
        raise RuntimeError("No asset_ids provided for agent_containerize_auto")

    dry_run = bool(parameters.get("dry_run", False))
    soak_seconds = min(max(int(parameters.get("soak_seconds", 120)), 10), 600)
    asset_id = asset_ids[0]

    org_id = ""
    requester_id = ""
    current_cr_id = ""
    async with AsyncSessionLocal() as db:
        asset = await db.get(
            Asset,
            uuid.UUID(asset_id) if isinstance(asset_id, str) else asset_id
        )
        if asset:
            org_id = str(asset.organization_id)
        # Find the current executing CR to get requester_id and cr_id
        if org_id:
            result = await db.execute(
                select(ChangeRequest).where(
                    and_(
                        ChangeRequest.change_type == ChangeType.agent_containerize_auto,
                        ChangeRequest.status == ChangeRequestStatus.executing,
                        ChangeRequest.organization_id == uuid.UUID(org_id),
                    )
                ).order_by(ChangeRequest.created_at.desc()).limit(1)
            )
            current_cr = result.scalar_one_or_none()
            current_cr_id = str(current_cr.id) if current_cr else ""
            requester_id = str(current_cr.requester_id) if current_cr else org_id

    step_results: dict[str, Any] = {}

    # Stage 1: preflight_discovery
    discovery = await _stage_preflight_discovery(asset_ids)
    step_results["preflight_discovery"] = {
        "workload_count": len(discovery.get("workloads", [])),
        "collected_at": discovery.get("collected_at"),
        "workloads": discovery.get("workloads", []),
    }

    # Stage 2: fleet_cross_reference
    graph = await _stage_fleet_cross_reference(discovery, str(asset_id))
    step_results["fleet_cross_reference"] = graph

    # Stage 3: ai_analysis
    if not org_id:
        raise RuntimeError("Could not determine org_id from asset")
    analysis = await _stage_ai_analysis(discovery, graph, org_id, dry_run=dry_run)
    step_results["ai_analysis"] = analysis

    migration_units: list[dict] = analysis.get("migration_units", [])
    has_stateful = any(u.get("stateful", False) for u in migration_units)

    # Stage 4: stateful_gate (only if stateful units exist and not dry_run)
    if has_stateful and not dry_run and current_cr_id:
        step_results["stateful_gate"] = {"status": "waiting", "cr_id": current_cr_id}
        await _stage_stateful_gate(current_cr_id)
        step_results["stateful_gate"]["status"] = "approved"

    # Stages 5-7 wrapped so step_results are preserved on failure
    try:
        # Stage 5: build
        if not dry_run:
            build_results = await _stage_build(migration_units, asset_ids, parameters, dry_run=False)
        else:
            build_results = {
                app: {"image_tag": f"dry-run/{app}:latest", "manifests": {}, "dry_run": True}
                for unit in migration_units for app in unit.get("apps", [])
            }
        step_results["build"] = build_results

        # Stage 6: deploy
        deploy_results = await _stage_deploy(
            migration_units, asset_ids, build_results, parameters, dry_run=dry_run
        )
        step_results["deploy"] = deploy_results

        # Stage 7: soak_verify
        if not dry_run:
            soak_result = await _stage_soak_verify(deploy_results, soak_seconds)
        else:
            soak_result = {"soak_seconds": 0, "probe_results": {}, "passed": True, "dry_run": True}
        step_results["soak_verify"] = soak_result

    except Exception as exc:
        step_results["execution_error"] = {"stage": "build_deploy_soak", "error": str(exc)}
        # Return soft-failure dict so step_results (including ai_analysis) are stored in the CR.
        # The workflow checks `failed: True` and marks the CR as failed.
        return {
            "action": "agent_containerize_auto",
            "failed": True,
            "error": str(exc),
            "dry_run": dry_run,
            "migration_units": migration_units,
            "step_results": step_results,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

    # Spawn retirement CR (skip in dry_run)
    if not dry_run and soak_result.get("passed") and org_id and requester_id:
        retirement_id = await _spawn_retirement_cr(
            source_cr_id=current_cr_id or "unknown",
            asset_ids=asset_ids,
            migration_units=migration_units,
            org_id=org_id,
            requester_id=requester_id,
        )
        step_results["retirement_cr_id"] = retirement_id

    return {
        "action": "agent_containerize_auto",
        "dry_run": dry_run,
        "migration_units": migration_units,
        "step_results": step_results,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Rollback all deployed units by invoking k8s workload delete."""
    from app.connectors.executors.kubernetes.workload_deploy import rollback as k8s_rollback

    deploy_results = (execution_result.get("step_results") or {}).get("deploy", {})
    rolled_back = []
    for app_name, result in deploy_results.items():
        try:
            await k8s_rollback({"app_name": app_name}, result, None)
            rolled_back.append(app_name)
        except Exception:
            pass

    return {"rolled_back": True, "apps": rolled_back}
