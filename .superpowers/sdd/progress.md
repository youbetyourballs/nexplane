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
- Plan B Task 2 (AKS node pool upgrade): complete (commits fc6f8ae..376db62, review clean — Minor: poller.wait() removed proactively (would fail live smoke), dead AgentPool import removed; no rollback unit test)
- Plan B Task 3 (AKS smoke tests): complete (commits 376db62..0cf14c6, review clean)
- Final review fixes: complete (commits 80d2656..81ff2dc — Critical: AD winrm_hostname field mismatch; Important: get_running_loop in create_ebs_snapshot, snapshot audit write isolation)

# Kernel Upgrade Plan
- Kernel Upgrade Task 1 (Go agent commands): complete (commits 81ff2dc..f3035d1, review clean after fix — Critical: commands registered in rollbacks map only, fixed to commands map; Important: dispatch shim missing build tag (cosmetic, no compile impact); Minor: verifyServicesOS stderr detail simplified)
- Kernel Upgrade Task 2 (Python executor): complete (commits f3035d1..b8c545b, review clean after fix — Critical: get_event_loop→get_running_loop, _wait_for_agent replaced health_check dispatch with DB last_seen_at poll; Minor: _take_snapshot signature adapted from real _snapshot_helpers API)
- Kernel Upgrade Task 3 (catalog + smoke test): complete (commits b8c545b..720908a, review clean — smoke_verified=false pending live run with real EC2 asset)
- Kernel Upgrade final review fixes: complete (commits 720908a..9470694 — Critical: rollbacks map purged of non-rollback commands, _wait_for_agent heartbeat race fixed with seen_before param, smoke fixture get_event_loop→asyncio.run; Important: service name regex validation, exec_result error check pre-reboot, GRUB option injection guard, Phase 3 smoke uses Phase 2 EBS snapshot)
