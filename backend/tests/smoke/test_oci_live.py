# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

#!/usr/bin/env python3
"""
Nexplane OCI Smoke Test — full connector lifecycle.

Runs phases OCI_A through OCI_N against a live OCI tenancy using the
connector credentials stored in the Nexplane database.

Usage:
    python backend/tests/smoke/test_oci_live.py \\
        --base-url http://localhost:8000 \\
        --email admin@acme.example \\
        --password admin123

Requirements:
    OCI connector configured in Nexplane with valid API key credentials.
"""
import sys
from smoke_helpers import make_base_parser, log

# Import the OCI worker from the multicloud test
from test_multicloud_live import run_oci_worker


def main():
    parser = make_base_parser("Nexplane OCI connector smoke test")
    args = parser.parse_args()

    print("=" * 60)
    print("Nexplane OCI Smoke Test")
    print("=" * 60)
    print("Phases: OCI_A (compartment/VCN) → OCI_B (compute) → OCI_C (lifecycle)")
    print("        OCI_D (snapshot) → OCI_F (storage) → OCI_G (block volumes)")
    print("        OCI_H (security/NSG) → OCI_J (DNS) → OCI_K (IAM)")
    print("        OCI_L (vault) → OCI_N (monitoring/logging)")
    print()

    result = run_oci_worker(args.base_url, args.email, args.password)

    print()
    print("=" * 60)
    print("OCI SMOKE TEST RESULTS")
    print("=" * 60)
    if result["passed"]:
        print("  ✅ OCI: PASSED")
        print("=" * 60)
        print("✅ ALL SELECTED PHASES PASSED")
    else:
        print(f"  ❌ OCI: FAILED — {result['error']}")
        print("=" * 60)
        print("❌ SMOKE TEST FAILED")
        sys.exit(1)


if __name__ == "__main__":
    main()
