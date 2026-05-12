# SP4 — Autonomous Containerization CR Design

## Goal
`agent_containerize_auto` works end-to-end: single CR migrates payments stack to Kubernetes with AI-assisted dependency analysis, stateful gate, and auto-retire.

## 7-Stage Flow (existing executor, needs wiring)

1. **deep_discover** — agent scans running processes, ports, outbound connections
   - Finds: payments-api connects to redis:6379, nginx connects to payments-api:5000
   - Redis identified as stateful (has /var/lib/redis with data)

2. **fleet_cross_reference** — cross-reference remote IPs against Nexplane inventory
   - Identifies if redis is on same host or separate asset

3. **ai_analysis** — structured prompt to AI service
   - Input: discovered workloads, port graph, stateful flags
   - Output: migration_units[], dependency_order, warnings[]
   - Redis classified as stateful → requires PVC + StatefulSet
   - Recommended deploy order: redis → payments-api → payments-web

4. **stateful_gate** — pause if any stateful unit found
   - CR transitions to awaiting_approval state
   - Operator sees: "Redis is stateful. Data migration required. Approve to continue."
   - Uses `POST /change-requests/{id}/confirm-stateful` endpoint

5. **build** — containerize_build for each unit in dependency order

6. **deploy** — k8s_workload_deploy for each unit

7. **soak_verify** — health checks with auto-rollback window (120s default)
   - All three services must return 2xx
   - If any fail: auto-rollback (delete workloads, restart systemd services)

8. **auto_spawn_retire** — on soak success, spawns agent_containerize_retire CR
   - Requires separate human approval before execution

## AI Prompt Design
The AI receives a structured JSON of discovered workloads and returns:
```json
{
  "migration_units": [
    {"name": "redis", "stateful": true, "k8s_kind": "StatefulSet", "pvc_size": "1Gi"},
    {"name": "payments-api", "stateful": false, "k8s_kind": "Deployment", "replicas": 2},
    {"name": "payments-web", "stateful": false, "k8s_kind": "Deployment", "replicas": 2}
  ],
  "deploy_order": ["redis", "payments-api", "payments-web"],
  "warnings": ["Redis has 45MB of data — manual data migration may be required"]
}
```

## Rollback Behaviour
- Stages 1-4: no external state, rollback is no-op
- Stage 5 (build): images pushed to ECR — rollback deletes ECR images
- Stage 6 (deploy): rollback deletes K8s workloads
- Stage 7 (soak): auto-rollback if health checks fail

## Smoke Test Coverage
Phase AUTO-A: Run agent_containerize_auto (dry_run=True), verify AI analysis output
Phase AUTO-B: Run with stateful gate, verify CR pauses at awaiting_approval
Phase AUTO-C: Approve stateful gate, run full flow, verify workloads deployed
Phase AUTO-D: Verify soak passes, retire CR spawned
