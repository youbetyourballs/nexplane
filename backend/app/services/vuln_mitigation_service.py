# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""AI-guided mitigation suggestion service for vulnerability findings."""
from __future__ import annotations
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_CONTROLS = {
    "network_isolation": ("Network isolation — restrict inbound ports", "Security group rule restricting inbound to trusted CIDRs.", ["network", "remote"]),
    "seccomp": ("seccomp profile — restrict syscalls", "Minimal-privilege seccomp profile blocking exploit syscalls.", ["local", "privesc", "memory"]),
    "apparmor": ("AppArmor/SELinux policy tightening", "MAC policy confining process filesystem and network access.", ["local", "privesc", "file"]),
    "waf": ("WAF rule — block exploit request pattern", "WAF rule blocking the specific request pattern the CVE exploits.", ["network", "web", "remote"]),
    "feature_flag": ("Feature flag / config disable", "Disable the vulnerable feature via env var or config file.", ["config", "network", "remote"]),
    "process_isolation": ("Process namespace isolation", "Isolate service into a separate namespace.", ["local", "privesc", "memory"]),
    "ebpf": ("eBPF runtime block", "eBPF probe intercepting the exploit syscall pattern.", ["local", "privesc", "memory", "network"]),
    "capability_drop": ("Linux capability drop", "Remove unneeded Linux capabilities.", ["local", "privesc"]),
    "rate_limit": ("Rate limiting / circuit breaker", "Aggressive rate limits raise exploitation cost.", ["network", "remote", "web"]),
    "virtual_patch": ("Virtual patch (IDS/IPS rule)", "Snort/Suricata rule detecting exploit traffic.", ["network", "remote"]),
    "full_isolation": ("Full network isolation — quarantine host", "Last resort deny-all. Causes service disruption.", ["network", "remote", "local"]),
}

_IMPACT = {
    "network_isolation": "low", "seccomp": "low", "apparmor": "low", "waf": "low",
    "feature_flag": "low", "process_isolation": "medium", "ebpf": "low",
    "capability_drop": "low", "rate_limit": "low", "virtual_patch": "low", "full_isolation": "high",
}


def _classify_cve(finding) -> list[str]:
    text = f"{finding.title or ''} {finding.description or ''}".lower()
    cats = []
    if any(w in text for w in ["remote", "network", "tls", "http", "tcp", "rce", "request"]): cats += ["network", "remote"]
    if any(w in text for w in ["web", "http", "sql", "xss", "csrf", "injection"]): cats.append("web")
    if any(w in text for w in ["privilege", "escalat", "root", "admin", "sudo"]): cats.append("privesc")
    if any(w in text for w in ["buffer", "overflow", "heap", "stack", "memory", "use-after"]): cats.append("memory")
    if any(w in text for w in ["file", "path", "traversal", "read", "write"]): cats.append("file")
    if any(w in text for w in ["config", "protocol", "version", "cipher", "ssl", "tls 1"]): cats.append("config")
    return cats or ["network"]


def _heuristic_suggest(finding, environment: str, criticality: str) -> list:
    from app.schemas.vulnerability import MitigationSuggestion
    categories = _classify_cve(finding)
    scored = []
    for cid, (title, desc, applicable) in _CONTROLS.items():
        overlap = len(set(categories) & set(applicable))
        if overlap == 0:
            continue
        base = overlap / max(len(categories), 1)
        if _IMPACT[cid] == "high" and criticality not in ("critical",):
            base *= 0.3
        if environment == "prod" and criticality == "critical" and cid in ("network_isolation", "seccomp", "feature_flag"):
            base = min(base * 1.3, 0.99)
        scored.append((base, cid))
    scored.sort(key=lambda x: -x[0])
    suggestions = []
    for i, (score, cid) in enumerate(scored):
        title, desc, _ = _CONTROLS[cid]
        suggestions.append(MitigationSuggestion(
            control=cid, title=title, description=desc,
            rationale=f"Confidence {int(score*100)}% — {'Recommended for this CVE type.' if i < 3 else 'Optional — defence in depth.'}",
            confidence=round(score, 2), impact=_IMPACT[cid], recommended=(i < 3),
        ))
    return suggestions


async def _ai_suggest(finding, environment: str, criticality: str) -> Optional[list]:
    try:
        import anthropic
        client = anthropic.Anthropic()
        prompt = f"""Analyze this vulnerability and suggest mitigations. Return JSON array only.
CVE: {finding.cve_id}, Title: {finding.title}, Severity: {finding.severity}, Environment: {environment}/{criticality}
[{{"control": "<id>", "confidence": 0.0-1.0, "rationale": "<why>", "recommended": true/false}}]
Control IDs: network_isolation, seccomp, apparmor, waf, feature_flag, process_isolation, ebpf, capability_drop, rate_limit, virtual_patch, full_isolation"""
        msg = client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=512, messages=[{"role": "user", "content": prompt}])
        import re
        match = re.search(r'\[.*\]', msg.content[0].text, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception as e:
        logger.debug(f"AI suggestion failed: {e}")
    return None


async def suggest_mitigations(finding, asset=None):
    from app.schemas.vulnerability import MitigationSuggestionsResponse
    env = getattr(asset, "environment", "unknown") or "unknown"
    crit = getattr(asset, "criticality", "medium") or "medium"
    if hasattr(env, "value"): env = env.value
    if hasattr(crit, "value"): crit = crit.value

    ai_result = await _ai_suggest(finding, env, crit)
    suggestions = _heuristic_suggest(finding, env, crit)

    if ai_result:
        ai_map = {r["control"]: r for r in ai_result if "control" in r}
        for s in suggestions:
            if s.control in ai_map:
                ai = ai_map[s.control]
                s.confidence = round((s.confidence + float(ai.get("confidence", s.confidence))) / 2, 2)
                if "rationale" in ai: s.rationale = ai["rationale"]
                s.recommended = ai.get("recommended", s.recommended)
        suggestions.sort(key=lambda x: (-x.confidence, not x.recommended))

    cats = _classify_cve(finding)
    summary = (f"{'Network-exploitable' if 'network' in cats else 'Local'} CVE. "
               f"Top controls pre-selected based on exploit vector and asset criticality ({crit}/{env}). "
               "All mitigations are reversible.")
    return MitigationSuggestionsResponse(finding_id=finding.id, suggestions=suggestions, ai_summary=summary)
