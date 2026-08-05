# Jenkins Rolling Upgrade Design

**Goal:** Upgrade a Jenkins instance to a target core version while validating plugin compatibility, exporting a JCasC configuration snapshot, and providing deterministic rollback across WAR, plugins, and configuration.

**Architecture:** Jenkins upgrades carry two coupled risks — core WAR version and plugin compatibility — so the CR addresses them in sequence: plugin compatibility is checked against the update center before any mutation, plugins are updated to compatible versions before the WAR is swapped, and JCasC export captures full configuration state for rollback. The executor connects to Jenkins via REST API for orchestration and SSH for filesystem operations (WAR replacement, backup staging). Quiet mode drains in-flight builds before any destructive step.

## Phases

1. **Preflight** — Connect to Jenkins REST API (`GET /api/json?tree=version`) and record current version; fetch update center state (`GET /updateCenter/api/json`); for each installed plugin, check `compatibleSinceVersion` against the target core version and surface incompatible plugins as blocking errors; confirm JCasC plugin is installed (required for config export — `GET /pluginManager/api/json?tree=plugins[shortName,version]`); check `GET /computer/api/json` for executors running jobs longer than 1 hour (warn, do not block); verify disk space in `JENKINS_HOME` for WAR and backup storage; confirm SSH connectivity to `ssh_host` and write access to `jenkins_home` and `backup_path`.

2. **Snapshot** — Export JCasC configuration: `GET /configuration-as-code/export` → save to `<backup_path>/casc-<timestamp>.yaml`; copy current WAR: `cp $JENKINS_HOME/jenkins.war <backup_path>/jenkins-<version>.war`; export plugin list: `GET /pluginManager/api/json?tree=plugins[shortName,version,active]` → save to `<backup_path>/plugins-<timestamp>.json`; copy all installed JPI files: `cp $JENKINS_HOME/plugins/*.jpi <backup_path>/plugins-<timestamp>/`; store all backup paths in CR execution context for rollback reference.

3. **Upgrade** — (1) Enable quiet mode: `POST /quietDown`; poll `GET /computer/api/json` until all executors idle or `quiet_mode_drain_timeout_seconds` elapses (configurable force-stop after timeout). (2) Update plugins first: for each plugin requiring update per the compatibility matrix, `POST /pluginManager/install`; wait for installation completion; `POST /safeRestart`; poll `GET /login` until Jenkins is responsive. (3) Download target WAR: `wget https://get.jenkins.io/war-stable/<version>/jenkins.war -O /tmp/jenkins-new.war`; verify SHA256 checksum against Jenkins release manifest if `war_checksum_verify=true`. (4) Replace WAR: `systemctl stop jenkins`; `cp /tmp/jenkins-new.war $JENKINS_HOME/jenkins.war`; `systemctl start jenkins`. (5) Poll `GET /api/json?tree=version` until target version is returned or timeout exceeded. (6) Disable quiet mode: `POST /cancelQuietDown`.

4. **Verify** — `GET /api/json?tree=version` returns the target version string; `GET /pluginManager/api/json` shows no plugins with `hasMandatoryUpdate: true` for security-critical plugins; `GET /computer/api/json` confirms executors are online and accepting builds; if a `test_job_name` parameter is provided, trigger it via `POST /job/<name>/build` and poll for green result; confirm JCasC re-export matches snapshot structure (no unexpected drift in configuration).

5. **Rollback** — `systemctl stop jenkins`; restore previous WAR: `cp <backup_path>/jenkins-<version>.war $JENKINS_HOME/jenkins.war`; restore plugin JPI files: `cp <backup_path>/plugins-<timestamp>/*.jpi $JENKINS_HOME/plugins/`; restart Jenkins: `systemctl start jenkins`; once responsive, apply JCasC backup: `POST /configuration-as-code/apply` with saved YAML body; poll for startup completion. Job history, artifacts, and workspace data on disk are never touched by the upgrade and require no restore action.

## CR Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `target_version` | string | yes | — | Target Jenkins version, e.g. `"2.426.3"` (LTS) or `"2.444"` (weekly) |
| `jenkins_url` | string | yes | — | Base URL of the Jenkins instance |
| `ssh_host` | string | yes | — | SSH host for WAR and filesystem operations |
| `jenkins_connector_id` | string | no | — | Connector ID providing Jenkins API credentials |
| `jenkins_user` | string | no | — | Jenkins user (overrides connector creds) |
| `jenkins_token` | string | no | — | Jenkins API token (overrides connector creds) |
| `jenkins_home` | string | no | `"/var/lib/jenkins"` | Path to JENKINS_HOME on the server |
| `backup_path` | string | no | `"/var/backup/jenkins"` | Directory for WAR, plugin, and JCasC backups |
| `quiet_mode_drain_timeout_seconds` | int | no | `1800` | Max time to wait for executors to drain before force-stopping |
| `plugin_update_strategy` | string | no | `"compatible"` | `"compatible"` updates only plugins required for core compatibility; `"all"` updates all available |
| `war_checksum_verify` | bool | no | `true` | Verify WAR SHA256 against Jenkins release manifest |
| `test_job_name` | string | no | — | Optional job to trigger as post-upgrade health check |
| `dry_run` | bool | no | `false` | Plan only — no Jenkins mutations or filesystem writes |

## Rollback Capability

**FULL.** WAR, all plugin JPI files, and the complete JCasC configuration export are captured before any mutation. WAR restore is a deterministic file copy followed by a service restart. Plugin restore replaces all JPI files from the backup directory — no dependency resolution needed. JCasC restore replays the exact exported configuration via the REST endpoint. Job history, build artifacts, and credentials stored in Jenkins are on-disk and unaffected by upgrade or rollback.

## Smoke Test Requirements

- Jenkins LTS on EC2 (same VPC as platform), version 2.414, with the JCasC plugin and at least three representative plugins installed
- Smoke phases: preflight against live instance → snapshot exports → plugin compatibility check with intentional incompatible plugin to verify blocking behavior → upgrade to 2.426 → verify version and plugin state → execute rollback → confirm 2.414 is restored and JCasC config matches pre-upgrade export
- SSH connector and Jenkins API connector stored in platform DB; no manual credential injection

## Key Risks

- **Plugin incompatibility on startup** — an incompatible plugin causes Jenkins to fail to initialize after WAR swap, producing a blank or error UI with no API access. Mitigation: compatibility matrix is checked in preflight as a blocking gate; only plugins with `compatibleSinceVersion <= target` are permitted; incompatible plugins are listed in the plan output so the operator can decide before approval.
- **Quiet mode drain timeout with long-running jobs** — production Jenkins instances may have jobs running for hours; force-stopping them loses build state. Mitigation: `quiet_mode_drain_timeout_seconds` defaults to 30 minutes and is surfaced prominently in the plan; operator sets a maintenance window appropriate to their job durations; the plan phase reports currently-running jobs and their durations.
- **JCasC plugin not installed** — without JCasC, configuration snapshot and restore are unavailable, degrading rollback fidelity. Mitigation: preflight fails with a clear error if JCasC is absent and offers an automated fix (install JCasC plugin) as a separate plan step the operator can approve before proceeding with the upgrade.
