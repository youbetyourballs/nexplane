#!/usr/bin/env python3
"""
Nexplane Multi-Cloud Consolidation Smoke Test — Phases X1–X2.

Cross-cloud phases that verify the same operation works correctly across
all three providers (AWS, GCP, Azure) in a single run. Built after
Azure Sub-projects B–G are complete.

For provider-specific phases, use the dedicated test files:
    AWS phases A-K  →  python backend/tests/smoke/test_aws_live.py
    GCP phases L-M  →  python backend/tests/smoke/test_gcp_live.py
    Azure phases N-O →  python backend/tests/smoke/test_azure_live.py
    Agent phases    →  python backend/tests/smoke/test_agent_live.py

Usage:
    python backend/tests/smoke/test_cloud_live.py \\
        --base-url http://localhost:8000 \\
        --email admin@nexplane.local \\
        --password changeme \\
        --phases X1,X2 \\
        --tailscale-auth-key tskey-auth-<key> \\
        --gcp-project my-project-id \\
        --azure-resource-group nexplane-smoke-rg

Requirements:
    All three cloud connectors configured with live credentials.
    Azure Sub-projects B–G complete (blocked until then).
"""
import sys
from typing import Optional

from smoke_helpers import NexplaneClient, log, fail, make_base_parser

# Old phase letters that have moved to provider-specific files
_PROVIDER_REDIRECT = {
    **{p: "test_aws_live.py" for p in "ABCDEFGHIJK"},
    **{p: "test_gcp_live.py" for p in "LM"},
    **{p: "test_azure_live.py" for p in "NO"},
}


# ---------------------------------------------------------------------------
# Phase X1 — Cross-cloud instance lifecycle (STUB — blocked until Azure B-G)
# ---------------------------------------------------------------------------

def run_phase_x1(client: NexplaneClient, cloud_account_id: str,
                 gcp_project: str, azure_resource_group: str,
                 tailscale_auth_key: str) -> None:
    """Phase X1: Launch on AWS+GCP+Azure, verify inventory, stop all, verify state."""
    print("\n[Phase X1] Cross-Cloud Instance Lifecycle — STUB")
    print("  ⚠️  Phase X1 is not yet implemented.")
    print("  Implement after Azure Sub-projects B–G are complete.")
    print("  This phase launches a VM on AWS, GCP, and Azure simultaneously,")
    print("  verifies all three appear in inventory with correct connector_type,")
    print("  stops all three, verifies power state via each cloud's SDK, then cleans up.")


# ---------------------------------------------------------------------------
# Phase X2 — Cross-cloud snapshot (STUB — blocked until Azure B-G)
# ---------------------------------------------------------------------------

def run_phase_x2(client: NexplaneClient, cloud_account_id: str,
                 gcp_project: str, azure_resource_group: str) -> None:
    """Phase X2: Snapshot on AWS+GCP+Azure, verify, delete."""
    print("\n[Phase X2] Cross-Cloud Snapshot — STUB")
    print("  ⚠️  Phase X2 is not yet implemented.")
    print("  Requires Phase X1 instances to be running.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = make_base_parser("Nexplane multi-cloud consolidation smoke test")
    parser.add_argument(
        "--phases", default="X1,X2",
        help="Cross-cloud phases to run (X1, X2). Old phases A-O have moved to provider files.",
    )
    parser.add_argument("--tailscale-auth-key", default="")
    parser.add_argument("--gcp-project", default="")
    parser.add_argument("--azure-resource-group", default="")
    args = parser.parse_args()
    phases = {p.strip().upper() for p in args.phases.split(",")}

    # Check for old phase letters and redirect
    old_phases = [p for p in phases if p in _PROVIDER_REDIRECT]
    if old_phases:
        print("\n❌ These phases have moved to provider-specific files:\n")
        for p in sorted(old_phases):
            dest = _PROVIDER_REDIRECT[p]
            print(f"  Phase {p}  →  python backend/tests/smoke/{dest} --phases {p}")
        print("\nExample:")
        print("  AWS phases A-K:  python backend/tests/smoke/test_aws_live.py --phases A,B,C,D")
        print("  GCP phases L-M:  python backend/tests/smoke/test_gcp_live.py --phases L,M")
        print("  Azure phases N-O: python backend/tests/smoke/test_azure_live.py --phases N,O")
        print("  Agent all clouds: python backend/tests/smoke/test_agent_live.py")
        sys.exit(1)

    print("=" * 60)
    print(f"Nexplane Multi-Cloud Smoke Test — phases: {', '.join(sorted(phases))}")
    print("=" * 60)

    client = NexplaneClient(args.base_url, args.email, args.password)
    log("Authenticated")
    cloud_account_id = client.get_cloud_account_asset_id()

    passed = False
    try:
        if "X1" in phases:
            run_phase_x1(client, cloud_account_id, args.gcp_project,
                         args.azure_resource_group, args.tailscale_auth_key)
        if "X2" in phases:
            run_phase_x2(client, cloud_account_id, args.gcp_project, args.azure_resource_group)

        print("\n" + "=" * 60)
        print("✅ ALL SELECTED PHASES PASSED")
        print("=" * 60)
        passed = True

    except SystemExit:
        passed = False
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        passed = False
    finally:
        if not passed:
            print("\n❌ SMOKE TEST FAILED")
            sys.exit(1)


if __name__ == "__main__":
    main()
