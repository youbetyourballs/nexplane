# MCP Deep Parity — SDD Progress Ledger

## Plan 1: Host Intelligence — COMPLETE
- Task 1: complete (commits 5ed72fa..4d58222, review clean)
- Task 2: complete (commits 4d58222..2ee3739, review clean)
- Tasks 3-4: complete (commits 2ee3739..e812dd5, review clean)
- Task 5: complete (commits e812dd5..de3cb4c, review clean) — NOTE: removed `from __future__ import annotations`; FastMCP crashes with it
- Task 6: complete (commit de6fefc, 18/18 smoke tests passed on EC2 in 179s)

## Plan 2: Planning Context
- Task 1: TODO
- Task 2: TODO
- Task 3: TODO
- Task 4: TODO
- Task 5: TODO

## Plan 3: Project Orchestration
- Task 1: TODO
- Task 2: TODO
- Task 3: TODO
- Task 4: TODO
- Task 5: TODO
- Task 6: TODO
- Task 7: TODO

- Plan 2 Task 1: complete (commits de6fefc..05e186e, review clean)
- Plan 2 Task 2: complete (commits 05e186e..7c9d9d0, review clean)
- Plan 2 Task 2: complete (commits 05e186e..7c9d9d0, review clean)
- Plan 2 Task 3: complete (commits 7c9d9d0..d411e6a, review clean)
- Plan 2 Task 4: complete (commits d411e6a..02f03b1, review clean — reviewer false alarm on _uuid import verified present at line 466)
- Plan 2 Task 4: complete (commits d411e6a..02f03b1, review clean)
- Plan 2 Task 5 (registration + migration): complete (commit 72319c5, 20/20 tests)
- Plan 2 Task 5b (smoke): complete (commit 51779ed, 19/19 tests passed on EC2; gap found: change_type PG enum crash fixed)
## Plan 3: Project Orchestration
- Plan 3 Task 1: complete (commits 51779ed..fb9bd2f, review clean)
- Plan 3 Task 2: complete (commits fb9bd2f..1ab7466, review clean)
- Plan 3 Task 3: complete (commits 1ab7466..e99ef66, review clean after fix for lazy-load + executor field)
- Plan 3 Task 4: complete (commits e99ef66..7626e3d, review clean)
- Plan 3 Task 5: complete (commits 7626e3d..1037a75, review clean)
- Plan 3 Task 6: complete (commits 1037a75..d7a50d7, review clean)
- Plan 3 Task 7 (smoke): complete (commit c0f64e6, 12/12 phases passed on EC2; gaps found: AgentToken actor_id fix, response shape fixes for define_success_criteria/check_success_criteria/get_project/estimate_project_risk)

- Final review fix (C1/C2/I1/I2): complete (commit 575b588 — materialize_project_plan actor_id fix, host_intelligence AgentToken fix, port_check/service_check raw-dict fix, execute_change_request error surface)

## ALL THREE PLANS COMPLETE — All smoke suites passing after review fixes
- Host Intelligence: 18/18 ✓ (requires root agent — start with sudo)
- Planning Context: 19/19 ✓
- Project Orchestration: 12/12 ✓

## FILO Rollback Stack
- Task 1: complete (commits 54a8689..44f60d8, review clean — to_cr_id enforcement in rollback_project + 4 unit tests)
- Task 2: complete (commits 44f60d8..4b992be, review clean — application_sequence-first sort + divergence warning + 4 unit tests)
- Task 3: complete (commits 4b992be..52ef27c, review clean — 5-phase FILO smoke test, ready to run on EC2)

## FILO Smoke Provisioning
- Task 1: complete (commits f2211c6..12a298d, review clean — self-contained EC2 provision + agent deploy in FILO smoke test)

## Deferred items (not blockers)
- I3: Host-intelligence read tools dispatch agent jobs directly (bypass CR lifecycle). Design spec said they should create audit CRs. Accepted divergence for read-only data; update spec to match implementation.
- I4: project orchestration smoke has no live execute→rollback phase (PHASE_12 only tests draft project no-op). Full execute+rollback smoke requires a pre-provisioned approvable CR — deferred to a future smoke expansion session.
- M4: N+1 queries in get_fleet_context / get_migration_precedents — acceptable at current fleet sizes.

## Backup/Restore Plan
- Task 1: complete (commits 64a6d18..c51595d, review clean)
- Task 2: complete (commits c51595d..5bef7e8, review clean)
- Task 3: complete (commits 5bef7e8..232cbf3, review clean after fix � EBS snapshot deletion added to rollback)
- Task 4: complete (commits 232cbf3..c9a1a8c, review clean)
- Task 5: complete (commits c9a1a8c..dcaf694, review clean)
- Task 6: complete (commits dcaf694..99486aa, review clean)
- Task 7: complete (commits 99486aa..697e2e9, review clean)
- Task 8: complete (commits 697e2e9..7ebf6e3, review clean after SMOKE_IAM_PROFILE fix)
- Task 9 (live smoke): complete (commit edc6ada, ALL_PHASES_PASSED on live EC2)
  Fixes: connector cred field names (access_key_id not aws_*), _aws_connector_id forwarded
  through execution result for rollback, restore_server loads artifact_refs from
  execution_run.result.steps, smoke test extraction helpers + FILO rollback order
## BACKUP/RESTORE PLAN COMPLETE — All smoke phases passing on live EC2

## MCP Exhaustive Smoke (2026-07-03-mcp-smoke-exhaustive-plan.md)
- Task 1 (extend _invoke_mcp_tool_inprocess): complete (commit f567da0)
- Task 2 (MCP_FINDINGS phase): complete (commit f567da0, DB-verified severity/title)
- Task 3 (MCP_CONNECTORS phase): complete (commit f567da0, DB-verified + no cred leak)
- Task 4 (MCP_IDENTITY phase): complete (commit f567da0, grounded non-error)
- Task 5 (MCP_RUNBOOKS phase): complete (commit f567da0, DB-verified name)
- Task 6 (MCP_HOST_INTEL phase): complete (commit f567da0, 16 tools, graceful no-agent)
- Task 7 (MCP_PLANNING_CTX phase): complete (commit f567da0, get_asset_history DB-verified)
- Task 8 (MCP_MEMORY_ACCURACY phase): complete (commit f567da0, cross-tool consistency)
- Task 9 (EXPECTED_TOOLS expansion + full run): complete (commit f567da0, 15/15 phases passed on EC2 in 81s)
  Fixes: connectors.py get_catalog→get_catalog_service, connector_type enum normalization,
  _run_db_check fresh-engine helper, host_intel RuntimeError graceful handling,
  get_kernel_eol_status/get_environment_diff/get_project_precedents signature corrections
## MCP EXHAUSTIVE SMOKE COMPLETE — 15/15 phases passing on live EC2
- Task 1: complete (commits b229be8..ad8b904, review clean — commercial load gate, 5/5 unit tests)
- Task 2: complete (commits ad8b904..edffece, review clean — 3/3 phases passed on EC2; field name fixes: plan steps in change_plan.generated_steps, result in execution_runs[0].result)
- Task 1: complete (commits af0c4a5..a10d144, review clean — upload/download/delete interface, aws_utils.py, 8/8 tests)
- Task 2: complete (commits 50b2f2a..972e396, review clean — backup_strategies/, ebs_snapshot extracted, server_backup.py thin dispatcher, 12/12 tests)
- Task 3: complete (commits 23eb59f..948e163, review clean — restore_strategies/, launch_ami + in_place extracted, restore_server.py thin dispatcher, 16/16 tests)
- Task 4: complete (commit 1fa98fa, review clean — backup001 migration, down_revision=bkp002 correct (actual head), backup_tier + capture_strategy added, 18/18 tests)
- Task 5: complete (commits 8eb8100..457ed18, review clean — local_files + file_restore_to_path, 22/22 tests)
- Task 6: complete (commit 430e4aa, review clean — B0 SSM sentinel write, B4 mandatory approval, assert pattern consistent with existing file)
- Task 7: complete (commit 12c427c, ALL_PHASES_PASSED on live EC2 — R2 SSM UUID verification, B0 SSM wait, server_capture/recurring_job_service fixes)
## BACKUP/RESTORE ARCHITECTURE PLAN COMPLETE — All smoke phases passing on live EC2
- Final review fix: complete (commit 2ce5d43 — put_bytes/delete_prefix public contract, restore artifact_refs in strategy results)
## BACKUP/RESTORE ARCHITECTURE PLAN COMPLETE AND APPROVED FOR MERGE
## Backup Target UI Plan
- Task 1: complete (commits 360e43f..1aeb0aa, review clean — BackupTargetCreate/Read extended, PATCH + recommend-strategy endpoints, 5/5 tests)
- Task 2: complete (commits 1aeb0aa..60914c4, review clean — BackupTarget/BackupTargetUpdate/StrategyRecommendation/BackupStorage types, updateTarget/recommendStrategy/listStorages added)
- Task 3: complete (commits 60914c4..17d4bda, review clean — BackupTargetForm slide-over, 4 backup types, recommend-strategy integration, inline warnings, create/edit mode)
- Task 4: complete (commits 17d4bda..54efaf2, review clean — BackupTargetForm wired into BackupRecovery + AssetDetail, TS baseline 6)
## BACKUP TARGET UI PLAN COMPLETE
- Final review fix: complete (commit 41d48ad — remove duplicate refetch, add 422 test, 6/6 tests)
## BACKUP TARGET UI PLAN COMPLETE AND APPROVED FOR MERGE

## Catalog Action Rollback (2026-07-04-catalog-action-rollback-plan.md)
Task 1: complete (commits c769554..9a74c35, review clean) — ChangeType enum + planning engine rollback wiring
Task 2: complete (commits 9a74c35..0925077, review clean) — _auto_asset_id threading + rollback asset cleanup
Task 3: complete (commits 0925077..28060db, review clean) — 3 smoke phases, 6/6 live on EC2, Alembic migration
Final fixes: 213152d — _delete_auto_asset flush instead of commit; migration downgrade comment
Task 1: complete (commits baa6a0e..8d9a8e7, review clean — 12 failing unit tests, _rollback_cr helper, stub test narrowed)
Task 2: complete (commits 8d9a8e7..f8f458f, review clean — storage_sync + STORAGE_SYNC smoke live PASSED)
Task 3: complete (commits f8f458f..bc183e3, review clean — lvm_snapshot + LVM_SNAPSHOT smoke live PASSED, stream dd|gzip fix, AZ filter fix)
Task 4: complete (commits bc183e3..4d5d234, review clean — nfs_files + combined LVM+NFS smoke live PASSED)
Task 5: complete (commits 4d5d234..bea6611, review clean — managed_db_snapshot + MANAGED_DB_SNAPSHOT smoke live PASSED. NOTE: rds:DeleteDBSnapshot IAM permission added imperatively to NexplaneEC2TestRole — needs Terraform codification in nexplane-infra)

## GCP Phases S-X (2026-07-06)
Base commit: 2289c44
- STU Task 1: complete (commits 2289c44..5e2da60, Phase S ✅ ALL SELECTED PHASES PASSED on EC2)
- STU Task 2: complete (commits 5e2da60..ce109ad, review clean — gcp_bucket_create/delete executors, catalog, change_type_defs, ChangeType enum, frontend types, unit tests)
- STU Task 3: complete (commits ce109ad..643ab29, Phase T ✅ PASSED on EC2; Alembic migration gcp001 added; PolicyBinding.get() bug fixed)
- STU Task 4: complete (commits 643ab29..7c777db, review clean — SA/IAM executors, catalog, change_type_defs, ChangeType enum; SA poll fix applied)
- STU Task 5: complete (Phase U ✅ PASSED on EC2 after granting roles/resourcemanager.projectIamAdmin to nexplane-dev@nexplane.iam.gserviceaccount.com)
- VWX Task 1: complete (commits a46f116..fc2b67c, review clean — DNS zone/record executors, catalog, change_type_defs, ChangeType enum)
- VWX Task 2: complete (commits fc2b67c..3e190f7, Phase V ✅ PASSED on EC2; reviewer no-raise finding adjudicated: brief was wrong, global constraint is authoritative)
- VWX Task 3: complete (commits 3e190f7..91773cf, review clean — Cloud SQL executors, catalog, change_type_defs, ChangeType; duration fix applied)
- VWX Task 4: complete (Phase W ✅ PASSED on EC2 after enabling sqladmin API + granting roles/cloudsql.admin to nexplane-dev SA; gcp_cloudsql_backup_create rollback_failed is expected — no meaningful backup undo)
- VWX Task 5: complete (commits 91773cf..ead43a3, review clean — Cloud Monitoring executors, false-alarm Phase W no-raise adjudicated: no-raise is authoritative pattern per Phase N/V)
- VWX Task 6: complete (commits ead43a3..88c2fe4, review clean — Phase X smoke ALL_PHASES_PASSED on EC2; fixes: google-cloud-monitoring dep, MonitoredResource pb2 import, backend restart)

## Backup Restore Strategies Plan (2026-07-06)
Base commit: d952244
- Task 1: complete (commits d952244..f2e94cb, review clean — GCS backend + list_prefix. Minor: s3.list_prefix uses bare key access; unused test imports)
- Task 2: complete (commits f2e94cb..1a05f12, review clean — storage_restore, 12/12 tests on EC2. Fix: module-level imports for testability)
- Task 3: complete (commits 1a05f12..9414213, review clean — database_dump MySQL+MongoDB, 3/3 tests on EC2. Fixes: module-level imports, asyncio.coroutine removal)
- Task 4: complete (commits 9414213..6b7bbd1, review clean — database_restore Postgres/MySQL/MongoDB, 5/5 tests on EC2. Fixes: SQL identifier quoting in DROP, tempfile.mktemp→NamedTemporaryFile, MySQL SELECT 1 verify assertion)
- Task 5: complete (commits 6b7bbd1..9a32b84, ALL SELECTED PHASES PASSED on EC2 — STORAGE_RESTORE ✅, DB_RESTORE/mysql ✅, DB_RESTORE/mongodb ✅. GCS_BACKEND skipped (no GCP bucket connector). Fixes: authorized_keys perms, disk prune, mariadb105/mongodb tools installed, CREATE DATABASE IF NOT EXISTS before mysql restore, skip auth args for unauthenticated MongoDB, redirect mongodump stderr to prevent paramiko deadlock)
## BACKUP RESTORE STRATEGIES PLAN COMPLETE — Final review: Ready to merge
- Deferred I1 RESOLVED: MongoDB rollback auth guard fixed (commit 7defbca)
- Deferred GCS_BACKEND RESOLVED: bucket nexplane-smoke-backup-test created in GCP project nexplane; gcs_bucket field added to GCP connector creds; GCS_BACKEND ✅ PASSED on EC2 (commit 0d7093f)
- Deferred DB_RESTORE/postgres RESOLVED: rewritten to use self-contained Docker postgres:15 on port 5433 (no RDS required); DB_RESTORE/postgres ✅ PASSED on EC2 (commit 0d7093f)
## ALL DEFERRED ITEMS RESOLVED — GCS_BACKEND ✅, DB_RESTORE/postgres ✅, I1 ✅

## Post-completion fixes (2026-07-07)
- gcp_cloudsql_backup_create rollback_failed fix: _rollback_no_op sentinel in rollback_executor.py (commit 3e04c6b); 4 unit tests; 4/4 passing
- import_image restore strategy: full implementation (commit bdafa39); 4 unit tests; 38/38 test_backup_architecture.py passing on EC2
- disk2vhd test fix: size check is SSM call #3 not #4 (commit bdafa39)
