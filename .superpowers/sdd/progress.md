# Cross-Cloud Parity — SDD Progress Ledger

Plans:
- Plan A: docs/superpowers/plans/2026-08-15-cross-cloud-dnssec.md
- Plan B: docs/superpowers/plans/2026-08-16-cross-cloud-aks.md
- Plan C: docs/superpowers/plans/2026-08-16-cross-cloud-serverless-containers.md
- Plan D: docs/superpowers/plans/2026-08-16-cross-cloud-container-registry.md
- Plan E: docs/superpowers/plans/2026-08-16-cross-cloud-baseline-hardening.md
- Plan F: docs/superpowers/plans/2026-08-16-cross-cloud-managed-service-upgrades.md

Base commit: be87e68

## Progress
- Plan A Task 1 (GCP Cloud DNS DNSSEC): complete (commits be87e68..4d03036, review clean after fix — Important: fresh credentials per closure, rollback_strategy field added; Minor: dead AsyncMock import, _poll defined in loop, no rollback unit test)
- Plan A Task 2 (OCI DNS DNSSEC): complete (commits 4d03036..a5c4754, review clean — Minor: missing rollback_strategy field in oci.json, _poll defined in loop, no rollback unit test)
- Plan A Task 3 (BIND DNS DNSSEC): complete (commits a5c4754..d2d7985, review clean after fixes — Important: shell injection sanitization added for key names, -b 256 dropped from ECDSAP256SHA256; Minor: dispatch import path difference, no explicit rollback_action: null in catalog, operator params not sanitized)
- Plan A Task 4 (DNSSEC smoke tests): complete (commits d2d7985..f9cf0d0, review clean after fixes — BIND double-env skipif, conftest credential guards; Minor: MagicMock fixture placeholder noted)
- Plan B Task 1 (AKS cluster upgrade): complete (commits f9cf0d0..fc6f8ae, review clean after fixes — Critical: rollback_strategy+generic_action added to catalog, dead ManagedCluster import removed; Important: _parse_minor raises ValueError, async mock fixed; Minor: no blast_radius_hint, resource_group absent from already_at_version result)
