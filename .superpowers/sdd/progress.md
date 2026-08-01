# App Upgrade CR Types — SDD Progress Ledger

Plans:
- Plan A: docs/superpowers/plans/2026-07-31-app-upgrade-plan-a-elasticsearch-opensearch.md
- Plan B: docs/superpowers/plans/2026-07-31-app-upgrade-plan-b-rabbitmq.md
- Plan C: docs/superpowers/plans/2026-07-31-app-upgrade-plan-c-kafka.md
- Plan D: docs/superpowers/plans/2026-07-31-app-upgrade-plan-d-java-python.md

Base commit: 378104d

## Progress
- Plan A Task 1: complete (commits 378104d..ca2e2cd, review clean — Minor: asyncio.get_event_loop deprecated, preflight_result not asserted in happy_path test)
- Plan A Task 2: complete (commits 2be7294..259d1cf, review clean — migration applied live, all 7 enum values verified)
- Plan A Task 3: complete (commits 206e075..0e55cc6, review clean after fixes: helpers.go extracted, upgraded_port added, curl→esPost flush, stop error logged)
- Plan A Task 4: complete (commits 78bcfc0..28858de, review clean after fixes: module-level execute/rollback, ROLLBACK_CAPABILITY, verify via HTTP not agent command, _snapshot_local override, catalog executor field)
- Plan A Task 5: complete (commits af971c4..d5a543e, review clean after fix: esGet/esPut parameter order corrected in helpers.go + all callers)
- Plan A Task 6: complete (commits 54dec21..c90d58f, smoke PASSED — ES 7.17.26→8.14.3 on port 19201, canary index survived, rollback_failed expected with skip_snapshot=True)
- Plan A Task 7: complete (commits c90d58f..922102c, smoke PASSED — OS 1.3.19→2.x on port 19201, canary survived, rollback_failed expected; fixed catalog entry + safety_engine registrations for all 6 new upgrade types)
- Plan B Task 1: complete (commits 922102c..cabefce, review clean after fix: http.NewRequest error handling in rmqGet/rmqPost)
- Plan B Task 2: complete (commits cabefce..b10a51f, review clean — catalog live-verified True; executor_registry.py not needed, dynamic import via catalog JSON)
- Plan B Task 3: complete (commits b10a51f..d2a5bbb, smoke PASSED 1 passed 72s — RMQ 3.12→4.0; rebuilt agent binary on EC2 — stale binary was blocking; Minor: version fallback assertion too broad)
- Plan C Task 1: complete (commits d2a5bbb..f268c16, review clean — 7 Kafka commands, go build clean; Minor: unreachable else + silent errors in bridge commands)
- Plan C Task 2: complete (commits f268c16..ee32281, review clean — both executors + catalog live-verified True True; fixed kafka_zk_container missing from bridge catalog entry)
- Plan C Task 3: complete (commits ee32281..d8b15db, smoke PASSED 1 passed 66s — bridge+cutover; fixed ROLLBACK_CAPABILITY "none"→"irreversible" + ROLLBACK_REASON; Minor: broad assertions due to no real KRaft env)
- Plan D Task 1: complete (commits d8b15db..ff0e16f, review clean after fix: include previous_image in upgrade result)
- Plan D Task 2: complete (commits ff0e16f..af9b03e, review clean after fixes: pip install via tempfile+docker cp, pip failure returns error, restore best-effort on new container)
- Plan D Task 3: complete (commits af9b03e..ed0384d, review clean — java+python executors + catalog live-verified True True; ChangeType enum + safety_engine entries already present from prior task)
- Plan D Task 4: complete (commits ed0384d..909b1b0, smoke PASSED 1 passed 51s — java_runtime_upgrade 11→17; Minor: JavaPreflightExecute runs on host not in container, java-11-amazon-corretto installed on EC2)
- Plan D Task 5: complete (commits 909b1b0..7339c9c, smoke PASSED 1 passed 55s — python_runtime_upgrade 3.8→3.11; pre-installed requests in container so pip freeze path taken)
- Final review: complete (commits 378104d..7339c9c reviewed, fix commit 63581e5 — C1: all 7 rollback signatures corrected; I1: kafka_kraft_cutover removed from _IMPLICIT_ROLLBACK_TYPES; I2: RabbitMQ rollback warning added)
