from __future__ import annotations
from typing import Any

CIS_V8_CONTROLS: list[dict] = [
    {"id": 1,  "name": "Inventory and Control of Enterprise Assets",        "method": "asset_coverage", "benchmark_sections": []},
    {"id": 2,  "name": "Inventory and Control of Software Assets",          "method": "not_tracked",    "benchmark_sections": []},
    {"id": 3,  "name": "Data Protection",                                   "method": "not_tracked",    "benchmark_sections": []},
    {"id": 4,  "name": "Secure Configuration of Enterprise Assets and Software", "method": "policy_baseline", "benchmark_sections": ["1.", "3."]},
    {"id": 5,  "name": "Account Management",                                "method": "agent_audit",    "benchmark_sections": ["5.1", "5.2"]},
    {"id": 6,  "name": "Access Control Management",                         "method": "agent_audit",    "benchmark_sections": ["5.3", "5.4"]},
    {"id": 7,  "name": "Continuous Vulnerability Management",               "method": "vuln_sla",       "benchmark_sections": ["2."]},
    {"id": 8,  "name": "Audit Log Management",                              "method": "agent_audit",    "benchmark_sections": ["4."]},
    {"id": 9,  "name": "Email and Web Browser Protections",                 "method": "not_tracked",    "benchmark_sections": []},
    {"id": 10, "name": "Malware Defenses",                                  "method": "edr_coverage",   "benchmark_sections": []},
    {"id": 11, "name": "Data Recovery",                                     "method": "not_tracked",    "benchmark_sections": []},
    {"id": 12, "name": "Network Infrastructure Management",                 "method": "not_tracked",    "benchmark_sections": []},
    {"id": 13, "name": "Network Monitoring and Defense",                    "method": "not_tracked",    "benchmark_sections": []},
    {"id": 14, "name": "Security Awareness and Skills Training",            "method": "not_tracked",    "benchmark_sections": []},
    {"id": 15, "name": "Service Provider Management",                       "method": "not_tracked",    "benchmark_sections": []},
    {"id": 16, "name": "Application Software Security",                     "method": "not_tracked",    "benchmark_sections": []},
    {"id": 17, "name": "Incident Response Management",                      "method": "not_tracked",    "benchmark_sections": []},
    {"id": 18, "name": "Penetration Testing",                               "method": "not_tracked",    "benchmark_sections": []},
]


def _section_matches(section: str, prefixes: list[str]) -> bool:
    for p in prefixes:
        if p.endswith("."):
            if section.startswith(p) or section == p.rstrip("."):
                return True
        else:
            if section == p or section.startswith(p + "."):
                return True
    return False


def compute_cis_summary(
    assets: list[dict[str, Any]],
    policy_baseline_counts: dict[str, int] | None = None,
    vuln_sla_data: dict[str, int] | None = None,
    edr_asset_ids: set[str] | None = None,
) -> dict[str, Any]:
    """
    Compute CIS Controls v8 summary.

    Extra data params (provided by the compliance router to avoid circular imports):
    - policy_baseline_counts: {"total_servers": N, "hardened_count": N} for Control 4
    - vuln_sla_data: {"total": N, "within_sla": N} for Control 7
    - edr_asset_ids: set of asset IDs covered by an EDR connector for Control 10
    """
    last_updated: str | None = None
    for asset in assets:
        ts = (
            (asset.get("asset_metadata") or {})
            .get("cis_compliance", {})
            .get("latest", {})
            .get("collected_at")
        )
        if ts:
            # Assumes collected_at values are UTC ISO 8601 (e.g. "2026-05-08T12:00:00Z").
            # Lexicographic comparison is valid only for this normalised format.
            if last_updated is None or ts > last_updated:
                last_updated = ts

    controls_out = []
    scored_values: list[float] = []

    for ctrl_def in CIS_V8_CONTROLS:
        cid = ctrl_def["id"]
        method = ctrl_def["method"]
        name = ctrl_def["name"]
        sections = ctrl_def["benchmark_sections"]

        if method == "not_tracked":
            controls_out.append({
                "id": cid, "name": name, "method": method,
                "score": None, "assets_passing": None, "assets_total": None,
                "checks": [],
            })
            continue

        if method == "policy_baseline":
            # Control 4: score from PolicyBaseline records vs server assets
            if policy_baseline_counts is None:
                controls_out.append({
                    "id": cid, "name": name, "method": method,
                    "score": None, "assets_passing": None, "assets_total": None,
                    "checks": [],
                })
            else:
                total = policy_baseline_counts.get("total_servers", 0)
                hardened = policy_baseline_counts.get("hardened_count", 0)
                score = (hardened / total) if total > 0 else None
                if score is not None:
                    scored_values.append(score)
                controls_out.append({
                    "id": cid, "name": name, "method": method,
                    "score": score,
                    "assets_passing": hardened,
                    "assets_total": total,
                    "checks": [{
                        "id": "4.1",
                        "title": "Server assets with a PolicyBaseline record",
                        "pass_count": hardened,
                        "fail_count": max(0, total - hardened),
                        "failing_assets": [],
                    }],
                })
            continue

        if method == "vuln_sla":
            # Control 7: score from vulnerability findings within SLA
            if vuln_sla_data is None:
                controls_out.append({
                    "id": cid, "name": name, "method": method,
                    "score": None, "assets_passing": None, "assets_total": None,
                    "checks": [],
                })
            else:
                total_vulns = vuln_sla_data.get("total", 0)
                within_sla = vuln_sla_data.get("within_sla", 0)
                score = (within_sla / total_vulns) if total_vulns > 0 else None
                if score is not None:
                    scored_values.append(score)
                controls_out.append({
                    "id": cid, "name": name, "method": method,
                    "score": score,
                    "assets_passing": within_sla,
                    "assets_total": total_vulns,
                    "checks": [{
                        "id": "7.1",
                        "title": "Vulnerability findings remediated within SLA",
                        "pass_count": within_sla,
                        "fail_count": max(0, total_vulns - within_sla),
                        "failing_assets": [],
                    }],
                })
            continue

        if method == "edr_coverage":
            # Control 10: score from assets covered by EDR (CrowdStrike/Defender)
            total = len(assets)
            if edr_asset_ids is None or total == 0:
                controls_out.append({
                    "id": cid, "name": name, "method": method,
                    "score": None, "assets_passing": None, "assets_total": None,
                    "checks": [],
                })
            else:
                covered = len([a for a in assets if a.get("id") in edr_asset_ids])
                score = covered / total
                scored_values.append(score)
                controls_out.append({
                    "id": cid, "name": name, "method": method,
                    "score": score,
                    "assets_passing": covered,
                    "assets_total": total,
                    "checks": [{
                        "id": "10.1",
                        "title": "Assets covered by EDR agent (CrowdStrike/Defender)",
                        "pass_count": covered,
                        "fail_count": total - covered,
                        "failing_assets": [],
                    }],
                })
            continue

        if method == "asset_coverage":
            total = len(assets)
            # An asset is "tracked" for CIS Control 1 when it has an active connector link.
            # Assets with connector_id=None are treated as unmanaged/untracked.
            passing_assets = [a for a in assets if a.get("connector_id")]
            failing_assets = [a for a in assets if not a.get("connector_id")]
            score = (len(passing_assets) / total) if total > 0 else None
            if score is not None:
                scored_values.append(score)
            controls_out.append({
                "id": cid, "name": name, "method": method,
                "score": score,
                "assets_passing": len(passing_assets),
                "assets_total": total,
                "checks": [{
                    "id": "1.1",
                    "title": "All enterprise assets tracked with active connector",
                    "pass_count": len(passing_assets),
                    "fail_count": len(failing_assets),
                    "failing_assets": [
                        {"id": a["id"], "name": a["name"], "detail": "No connector linked"}
                        for a in failing_assets
                    ],
                }],
            })
            continue

        # method == "agent_audit"
        check_map: dict[str, dict] = {}
        assets_audited: list[dict] = []
        assets_passing_ctrl: list[dict] = []

        for asset in assets:
            raw_controls = (
                (asset.get("asset_metadata") or {})
                .get("cis_compliance", {})
                .get("latest", {})
                .get("controls", [])
            )
            relevant = [
                r for r in raw_controls
                if _section_matches(r.get("section", ""), sections)
            ]
            if not relevant:
                continue

            assets_audited.append(asset)
            asset_passes_ctrl = all(r.get("status") == "pass" for r in relevant)
            if asset_passes_ctrl:
                assets_passing_ctrl.append(asset)

            for r in relevant:
                check_key = r.get("id") or r.get("title", "unknown")
                if check_key not in check_map:
                    check_map[check_key] = {
                        "id": r.get("id", ""),
                        "title": r.get("title", r.get("id", "unknown")),
                        "pass_assets": [],
                        "fail_assets": [],
                    }
                if r.get("status") == "pass":
                    check_map[check_key]["pass_assets"].append(asset)
                else:
                    check_map[check_key]["fail_assets"].append({
                        "id": asset["id"],
                        "name": asset["name"],
                        "detail": f"Expected: {r.get('expected', '')}  Got: {r.get('actual', '')}",
                    })

        n_audited = len(assets_audited)
        score = (len(assets_passing_ctrl) / n_audited) if n_audited > 0 else None
        if score is not None:
            scored_values.append(score)

        checks_out = [
            {
                "id": data["id"],
                "title": data["title"],
                "pass_count": len(data["pass_assets"]),
                "fail_count": len(data["fail_assets"]),
                "failing_assets": data["fail_assets"],
            }
            for data in check_map.values()
        ]

        controls_out.append({
            "id": cid, "name": name, "method": method,
            "score": score,
            "assets_passing": len(assets_passing_ctrl),
            "assets_total": n_audited,
            "checks": checks_out,
        })

    overall = (sum(scored_values) / len(scored_values)) if scored_values else None

    return {
        "overall_score": overall,
        "tracked_controls": sum(1 for c in CIS_V8_CONTROLS if c["method"] != "not_tracked"),
        "last_updated": last_updated,
        "controls": controls_out,
    }
