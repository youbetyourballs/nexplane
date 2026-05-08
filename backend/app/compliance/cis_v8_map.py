from __future__ import annotations
from typing import Any

CIS_V8_CONTROLS: list[dict] = [
    {"id": 1,  "name": "Inventory and Control of Enterprise Assets",        "method": "asset_coverage", "benchmark_sections": []},
    {"id": 2,  "name": "Inventory and Control of Software Assets",          "method": "not_tracked",    "benchmark_sections": []},
    {"id": 3,  "name": "Data Protection",                                   "method": "not_tracked",    "benchmark_sections": []},
    {"id": 4,  "name": "Secure Configuration of Enterprise Assets and Software", "method": "agent_audit", "benchmark_sections": ["1.", "3."]},
    {"id": 5,  "name": "Account Management",                                "method": "agent_audit",    "benchmark_sections": ["5.1", "5.2"]},
    {"id": 6,  "name": "Access Control Management",                         "method": "agent_audit",    "benchmark_sections": ["5.3", "5.4"]},
    {"id": 7,  "name": "Continuous Vulnerability Management",               "method": "agent_audit",    "benchmark_sections": ["2."]},
    {"id": 8,  "name": "Audit Log Management",                              "method": "agent_audit",    "benchmark_sections": ["4."]},
    {"id": 9,  "name": "Email and Web Browser Protections",                 "method": "not_tracked",    "benchmark_sections": []},
    {"id": 10, "name": "Malware Defenses",                                  "method": "not_tracked",    "benchmark_sections": []},
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
    return any(section.startswith(p) for p in prefixes)


def compute_cis_summary(assets: list[dict[str, Any]]) -> dict[str, Any]:
    last_updated: str | None = None
    for asset in assets:
        ts = (
            (asset.get("asset_metadata") or {})
            .get("cis_compliance", {})
            .get("latest", {})
            .get("collected_at")
        )
        if ts:
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

        if method == "asset_coverage":
            total = len(assets)
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
                title = r.get("title", r.get("id", "unknown"))
                if title not in check_map:
                    check_map[title] = {"id": r.get("id", ""), "pass_assets": [], "fail_assets": []}
                if r.get("status") == "pass":
                    check_map[title]["pass_assets"].append(asset)
                else:
                    check_map[title]["fail_assets"].append({
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
                "title": title,
                "pass_count": len(data["pass_assets"]),
                "fail_count": len(data["fail_assets"]),
                "failing_assets": data["fail_assets"],
            }
            for title, data in check_map.items()
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
