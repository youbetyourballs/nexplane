# Service Mesh and CI/CD Tooling Upgrades Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement upgrade CR types for five service mesh and CI/CD systems — Istio control plane upgrade, Kong API Gateway upgrade, cert-manager upgrade, GitLab major version upgrade, and Jenkins upgrade — all via nexplane_agent with AMI-cached smoke tests.

**Architecture:** Istio runs against an EKS cluster (reuse existing K8s smoke infrastructure). Kong, cert-manager (needs K8s), GitLab, and Jenkins run on EC2 via agent. All follow preflight → snapshot → upgrade → verify → rollback. GitLab is the most complex: must hop through each major version sequentially with background-migration waits between hops. cert-manager's CRD upgrade is partially irreversible (CRDs can't be cleanly downgraded).

**Tech Stack:** Python asyncio executors, `nexplane_agent._dispatch.dispatch_agent_job`, kubectl/helm for K8s-based systems, pytest smoke using `NexplaneClient` + `get_connector_creds_from_db`, boto3 for AMI cache.

## Global Constraints

- All executors in `backend/app/connectors/executors/nexplane_agent/`
- `ROLLBACK_CAPABILITY` declared at module level
- `desired_outcome` is the only parameter channel
- ChangeType enum + DB migration + change_type_definition JSON + catalog entry required for each
- Istio and cert-manager smoke tests reuse the EKS cluster from existing k8s_cluster_upgrade smoke
- GitLab, Jenkins, Kong use EC2 AMIs; cache in SSM
- smoke_verified: false until smoke passes; flip to true in same commit
- GitLab: must validate major version hop chain in preflight — skip-version upgrades are unsupported and will corrupt the database

---

## CR Type 1: `istio_control_plane_upgrade`

**Files:**
- Create: `backend/app/connectors/executors/nexplane_agent/istio_control_plane_upgrade.py`
- Create: `backend/app/connectors/change_type_definitions/istio_control_plane_upgrade.json`
- Modify: `backend/app/models/change_request.py` — add `istio_control_plane_upgrade`
- Modify: `backend/app/connectors/catalog/nexplane_agent.json`
- Create: migration file for DB enum
- Create: `backend/tests/smoke/test_smoke_istio_upgrade.py`

**Parameters (all in `desired_outcome`):**
- `source_version`: e.g. `"1.20"` (required)
- `target_version`: e.g. `"1.21"` (required)
- `upgrade_strategy`: `"canary"` | `"in_place"` (default `"canary"`)
- `namespaces`: list of namespaces whose sidecars to migrate (default: all labeled `istio-injection=enabled`)
- `kubeconfig_path`: path to kubeconfig on agent host (default `~/.kube/config`)
- `dry_run`: bool

**Istio canary upgrade strategy (recommended):**
Install the new control plane revision alongside the old one. Migrate namespaces one by one by relabeling and rolling restarting their pods. Remove old control plane only after all sidecars are migrated. This means rollback is always possible until the old revision is removed.

**Executor flow (canary):**
1. Preflight: `dispatch_agent_job("istio_preflight", ...)` — `istioctl version`, validate +1 minor only, `kubectl get pods -n istio-system` (all running), verify `istioctl` binary matches `target_version` (download if not present)
2. Snapshot: record current revision label (`istio.io/rev`) on all labeled namespaces; store in execution_result
3. Install new revision: `dispatch_agent_job("istio_install_revision", ...)` — `istioctl install --set revision=<target-rev> -y`; wait for new control plane pods Running
4. Per namespace: `dispatch_agent_job("istio_migrate_namespace", ...)` per namespace
   - Remove `istio-injection=enabled` label, add `istio.io/rev=<target-rev>` label
   - `kubectl rollout restart deployment -n <namespace>` — triggers sidecar re-injection
   - Wait for all pods Ready
5. Verify all sidecars: `istioctl proxy-status` — all proxies on new version
6. Remove old revision: `istioctl uninstall --revision <source-rev> -y`
7. Return: `{status, source_version, target_version, namespaces_migrated, upgraded_at}`

**Executor flow (in_place):** `istioctl upgrade -y` — simpler but no canary safety net. Only use if explicitly requested.

**Rollback:** ROLLBACK_CAPABILITY = `"full"` (canary) — re-label namespaces to old revision, rolling restart pods, remove new revision. Old control plane is still running until step 6. After step 6 (old removed): irreversible — surface this in execution_result.

**Smoke test:** Reuse EKS cluster from k8s_cluster_upgrade smoke. Install Istio 1.20 via `istioctl install` in setup, deploy a test namespace with `istio-injection=enabled` and a simple nginx pod. CR lifecycle: canary upgrade 1.20→1.21. Assert `istioctl proxy-status` all 1.21. Rollback (before old removed). Teardown: `istioctl uninstall --purge`.

---

## CR Type 2: `kong_upgrade`

**Files:**
- Create: `backend/app/connectors/executors/nexplane_agent/kong_upgrade.py`
- Create: `backend/app/connectors/change_type_definitions/kong_upgrade.json`
- Modify: `backend/app/models/change_request.py` — add `kong_upgrade`
- Modify: `backend/app/connectors/catalog/nexplane_agent.json`
- Create: migration file for DB enum
- Create: `backend/tests/smoke/test_smoke_kong_upgrade.py`

**Parameters:**
- `source_version`: e.g. `"3.4"` (required)
- `target_version`: e.g. `"3.7"` (required)
- `kong_host`: hostname/IP (default localhost)
- `kong_admin_port`: default 8001
- `kong_proxy_port`: default 8000
- `db_mode`: `"postgres"` | `"dbless"` (default `"postgres"`)
- `db_host`, `db_port`, `db_name`, `db_user`, `db_password`: required for postgres mode
- `dry_run`: bool

**Executor flow:**
1. Preflight: `GET /` on admin API — record version, plugin list, route/service/consumer counts; `kong check` config validation
2. Snapshot:
   - DB mode: `deck dump --output-file /tmp/nexplane-kong-backup.yaml`; DB dump via pg_dump
   - DB-less mode: copy declarative config file
3. Plugin compatibility check: `dispatch_agent_job("kong_check_plugins", ...)` — compare installed plugin versions against target Kong's bundled/supported versions; block if breaking incompatibility
4. Stop Kong: `systemctl stop kong`
5. Upgrade: `dispatch_agent_job("kong_install_version", ...)` — apt/yum install `kong=<target_version>`
6. Run migrations (DB mode): `kong migrations up` then `kong migrations finish`
7. Start Kong: `systemctl start kong`, wait for admin API `/`
8. Verify: `GET /` version matches target; `deck sync --dry-run` validates config round-trips cleanly; test proxy route
9. Return: `{status, source_version, target_version, routes_count, services_count, plugins_count, upgraded_at}`

**Rollback:** ROLLBACK_CAPABILITY = `"full"` — stop Kong, install old version package, run `kong migrations down` (DB mode), restore from deck dump / DB backup, start old Kong.

**Smoke test:** EC2 with Kong 3.4 + Postgres. Cached AMI `/nexplane/smoke-amis/kong/3.4`. Pre-seed one route and one service via deck. CR lifecycle: upgrade 3.4→3.7. Assert version, route still present, proxy returns 200. Rollback: assert 3.4 version responds.

---

## CR Type 3: `cert_manager_upgrade`

**Files:**
- Create: `backend/app/connectors/executors/nexplane_agent/cert_manager_upgrade.py`
- Create: `backend/app/connectors/change_type_definitions/cert_manager_upgrade.json`
- Modify: `backend/app/models/change_request.py` — add `cert_manager_upgrade`
- Modify: `backend/app/connectors/catalog/nexplane_agent.json`
- Create: migration file for DB enum
- Create: `backend/tests/smoke/test_smoke_cert_manager_upgrade.py`

**Parameters:**
- `source_version`: e.g. `"1.13"` (required)
- `target_version`: e.g. `"1.15"` (required)
- `namespace`: default `cert-manager`
- `kubeconfig_path`: path on agent host
- `dry_run`: bool

**cert-manager upgrade rule:** CRDs must be applied with `--server-side` before the Deployment is updated. CRDs cannot be cleanly downgraded (CRD schema changes are additive but not removable). Deployment-only rollback is always safe.

**Executor flow:**
1. Preflight: `kubectl get deployment cert-manager -n cert-manager` — current version from image tag; `kubectl get crds` — record current CRD resourceVersions; validate +1 minor (cert-manager supports skipping patch, not minor)
2. Snapshot: `kubectl get crds -o yaml > /tmp/cert-manager-crds-backup.yaml`; record current Deployment image tag
3. Upgrade CRDs: `dispatch_agent_job("cert_manager_upgrade_crds", ...)` — `kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v<target>/cert-manager.crds.yaml --server-side` — CRDs are now upgraded (POINT OF PARTIAL NO-RETURN)
4. Upgrade Deployment: `dispatch_agent_job("cert_manager_upgrade_deployment", ...)` — `kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v<target>/cert-manager.yaml`; wait for rollout `kubectl rollout status deployment/cert-manager -n cert-manager`
5. Verify: `kubectl get pods -n cert-manager` all Running; test Certificate issuance by creating a self-signed Certificate CR and polling until `Ready=True`
6. Return: `{status, source_version, target_version, crds_upgraded, deployment_upgraded, test_cert_issued, upgraded_at}`

**Rollback:** ROLLBACK_CAPABILITY = `"partial"` — Deployment can be rolled back (`kubectl rollout undo deployment/cert-manager -n cert-manager`). CRDs cannot be cleanly downgraded; old CRD backup is recorded but not restored (schema additions are non-breaking). Surface this clearly: deployment rolled back, CRDs remain at target version.

**Smoke test:** Reuse EKS cluster. Install cert-manager 1.13 in setup via Helm. CR lifecycle: upgrade 1.13→1.15. Assert Deployment running, test Certificate issued. Rollback: Deployment rolls back; CRDs remain (expected behavior).

---

## CR Type 4: `gitlab_upgrade`

**Files:**
- Create: `backend/app/connectors/executors/nexplane_agent/gitlab_upgrade.py`
- Create: `backend/app/connectors/change_type_definitions/gitlab_upgrade.json`
- Modify: `backend/app/models/change_request.py` — add `gitlab_upgrade`
- Modify: `backend/app/connectors/catalog/nexplane_agent.json`
- Create: migration file for DB enum
- Create: `backend/tests/smoke/test_smoke_gitlab_upgrade.py`

**Parameters:**
- `source_version`: e.g. `"15.11"` (required)
- `target_version`: e.g. `"17.2"` (required)
- `gitlab_host`: hostname/IP
- `gitlab_admin_token`: personal access token with admin scope
- `gitlab_package_type`: `"omnibus"` | `"helm"` (default `"omnibus"`)
- `backup_s3_bucket`: optional S3 bucket for gitlab-backup upload
- `dry_run`: bool

**GitLab hop chain:** GitLab requires upgrading through each major version in order (cannot skip). E.g. 15.11→16.0→16.11→17.0→17.2. The executor computes the required hops:
- Within same major: direct upgrade (15.4→15.11 is fine)
- Crossing major boundary: must hit the last minor of each major first (e.g. 16.0 requires 15.11 first; 17.0 requires 16.11 first)
- This is codified as: for each major between source and target, upgrade to `<major>.11` (last minor) before crossing to next major

**Executor flow:**
1. Preflight: `GET /api/v4/version` (record current version), compute hop chain, validate all intermediate versions are available as packages, check `GET /api/v4/migrations/status` (no stuck background migrations)
2. Per hop in chain:
   a. Snapshot: `dispatch_agent_job("gitlab_backup", ...)` — `gitlab-backup create SKIP=registry GZIP_RSYNCABLE=yes`; optionally upload to S3; store backup filename
   b. Upgrade: `dispatch_agent_job("gitlab_upgrade_hop", ...)` — `apt-get install -y gitlab-ee=<hop_version>`; `gitlab-ctl reconfigure`; `gitlab-ctl restart`
   c. Wait for background migrations: `dispatch_agent_job("gitlab_wait_migrations", ...)` — poll `GET /api/v4/migrations/status` until `status: complete` (up to 30 min per hop)
   d. Verify hop: `GET /api/v4/version` matches hop version; `gitlab-ctl status` all services running
3. Final verify: version matches target_version, `GET /api/v4/projects` returns 200, `GET /api/v4/users?active=true` returns users
4. Return: `{status, hops_completed, final_version, backup_paths, upgraded_at}`

**Rollback:** ROLLBACK_CAPABILITY = `"partial"` — GitLab background migrations are irreversible once run. Rollback restores from the backup taken at the start of the current hop. Each hop stores its own backup; rollback restores the most recent. Operator must re-run from that hop's backup point.

**Smoke test:** EC2 t3.xlarge (GitLab needs 4GB+ RAM). Cached AMI `/nexplane/smoke-amis/gitlab/15.11` (GitLab 15.11 omnibus installed). Single-hop smoke (15.11→16.0 — cheapest valid hop). Assert version=16.0, projects API returns 200. Rollback from backup (restore and verify 15.11). Note: full multi-hop smoke (15→17) is slow (~45 min); single-hop is sufficient for CR lifecycle verification.

---

## CR Type 5: `jenkins_upgrade`

**Files:**
- Create: `backend/app/connectors/executors/nexplane_agent/jenkins_upgrade.py`
- Create: `backend/app/connectors/change_type_definitions/jenkins_upgrade.json`
- Modify: `backend/app/models/change_request.py` — add `jenkins_upgrade`
- Modify: `backend/app/connectors/catalog/nexplane_agent.json`
- Create: migration file for DB enum
- Create: `backend/tests/smoke/test_smoke_jenkins_upgrade.py`

**Parameters:**
- `source_version`: e.g. `"2.426"` (required)
- `target_version`: e.g. `"2.452"` (required)
- `jenkins_home`: default `/var/lib/jenkins`
- `jenkins_war_path`: default `/usr/share/jenkins/jenkins.war`
- `jenkins_admin_url`: default `http://localhost:8080`
- `jenkins_admin_user`, `jenkins_admin_password`: (or API token)
- `dry_run`: bool

**Executor flow:**
1. Preflight: `GET /api/json?tree=version` — record current version; check all executors idle (`GET /computer/api/json?tree=totalExecutors,busyExecutors`) — warn if busy executors; `GET /pluginManager/api/json?tree=plugins[shortName,version,hasUpdate,dependencies]` — record all plugins
2. Snapshot: `dispatch_agent_job("jenkins_backup", ...)` — `cp -r <jenkins_home> /tmp/nexplane-jenkins-backup`; copy current WAR to `/tmp/nexplane-jenkins-old.war`
3. Enter quiet mode: `POST /quietDown` — stops accepting new builds (soft quiesce)
4. Wait for quiet: poll `GET /api/json?tree=quietingDown,executors[idle]` until all executors idle or 10 min timeout
5. Stop Jenkins: `systemctl stop jenkins`
6. Replace WAR: `dispatch_agent_job("jenkins_install_war", ...)` — download `jenkins.war` from `https://updates.jenkins.io/download/war/<version>/jenkins.war`, replace at `jenkins_war_path`
7. Start Jenkins: `systemctl start jenkins`, wait for `GET /api/json` to return 200 (up to 5 min)
8. Plugin compatibility: `dispatch_agent_job("jenkins_check_plugins", ...)` — `GET /updateCenter/coreSource/api/json` to check if any plugins report incompatibility; surface warnings but don't fail
9. Verify: `GET /api/json?tree=version` matches target; `GET /computer/api/json` executors available
10. Cancel quiet mode: `POST /cancelQuietDown`
11. Return: `{status, source_version, target_version, plugin_warnings, upgraded_at}`

**Rollback:** ROLLBACK_CAPABILITY = `"full"` — stop Jenkins, restore old WAR from `/tmp/nexplane-jenkins-old.war`, restore JENKINS_HOME from backup, start Jenkins. Note: any builds that ran between backup and rollback are lost.

**Smoke test:** EC2 with Jenkins 2.426 (WAR install, not package). Cached AMI `/nexplane/smoke-amis/jenkins/2.426`. CR lifecycle: upgrade 2.426→2.452. Assert version=2.452, executors available. Rollback: assert 2.426. Teardown.

---

## Backlog additions (cross-cloud parity)

- **GCP Cloud Run deploy** — serverless container deployment CR type; no equivalent to ECS rolling deploy yet
- **Azure Container Apps deploy** — same gap
- **OCI Container Instances deploy** — same gap
- **Azure AKS cluster/node pool management** — no AKS-specific executors; only generic k8s_cluster_upgrade works
