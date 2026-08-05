# GitLab Rolling Upgrade Design

**Goal:** Upgrade a GitLab omnibus instance (CE or EE) to a target version by automatically computing and executing the required intermediate stop versions, with a full backup-restore rollback path.

**Architecture:** GitLab enforces a strict minor-version upgrade path — skipping versions causes migration failures and data corruption. The executor computes the full hop sequence from the current version to the target using the canonical required-stop-version map, then iterates through each hop in order: drain Sidekiq, run Rails migrations, upgrade the package, reconfigure, and health-check before proceeding to the next hop. A full GitLab backup is taken before the first hop; rollback restores from that backup, making it a point-in-time full restore regardless of how many hops completed. This is intentional — mid-path state is not guaranteed consistent, so partial rollback is not offered.

## Phases

1. **Preflight** — Run `gitlab-rake gitlab:env:info` to record current version; verify all GitLab services (puma, sidekiq, gitaly, postgresql, redis, nginx) are running via `gitlab-ctl status`; check Sidekiq queue depth and warn if any queue exceeds 1000 jobs; verify at least 5 GB free on `/var/opt/gitlab`; compute the hop sequence from current to target version using the required-stop-version map and surface it for operator review; validate that each intermediate package version is available in the configured apt/yum repository.

2. **Snapshot** — Run `gitlab-backup create BACKUP=nexplane_upgrade` and record the output backup archive path; copy `/etc/gitlab/gitlab.rb` and `/etc/gitlab/gitlab-secrets.json` to `backup_path` with timestamps. The backup covers Git repositories, the PostgreSQL database, uploads, and CI artifacts. Secrets are backed up separately because the restore process requires them to match the backup exactly.

3. **Upgrade** — For each hop in the computed path: (1) Drain Sidekiq — send shutdown signal, wait up to `sidekiq_drain_timeout_seconds` for queues to empty; (2) Run Rails migrations — `gitlab-rake db:migrate`, verify no pending migrations remain; (3) Stop GitLab — `gitlab-ctl stop`; (4) Upgrade package — `apt-get install -y gitlab-ce=<hop-version>` or `gitlab-ee=<hop-version>`; (5) Reconfigure — `gitlab-ctl reconfigure`; (6) Start — `gitlab-ctl start`; (7) Health check — poll `GET /-/health` until HTTP 200 within `health_check_timeout_seconds`, then run `gitlab-rake gitlab:check SANITIZE=true`; (8) Repeat for next hop.

4. **Verify** — Confirm `gitlab-rake gitlab:env:info` reports the final target version; verify `gitlab-ctl status` shows all services running; confirm `GET /-/readiness` returns OK; run `gitlab-rails runner "puts User.count"` to validate database connectivity; confirm Sidekiq workers are active and processing.

5. **Rollback** — Restore the pre-upgrade backup: `gitlab-backup restore BACKUP=nexplane_upgrade`; restore `gitlab.rb` and `gitlab-secrets.json` from `backup_path`; downgrade the package to the pre-upgrade version; run `gitlab-ctl reconfigure && gitlab-ctl start`; confirm `/-/health` returns 200. Full rollback restores all data to the pre-upgrade state — any changes made to GitLab during the upgrade window (pushes, MR updates, pipeline runs) will be lost. The operator is warned of this during approval.

## CR Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `target_version` | string | yes | — | Target GitLab version, e.g. `"17.3.1"` |
| `edition` | string | no | `"ce"` | `"ce"` (Community) or `"ee"` (Enterprise) |
| `ssh_host` | string | yes | — | SSH host for the GitLab server |
| `gitlab_url` | string | yes | — | Public URL, e.g. `"https://gitlab.example.com"` |
| `admin_token` | string | no | from connector | GitLab personal access token with admin scope |
| `backup_path` | string | no | `"/var/opt/gitlab/backups"` | Directory for backup archives and config copies |
| `sidekiq_drain_timeout_seconds` | int | no | `60` | Max seconds to wait for Sidekiq queues to empty per hop |
| `health_check_timeout_seconds` | int | no | `300` | Max seconds to wait for `/-/health` per hop |
| `required_stop_versions` | list[string] | no | computed | Override the computed hop path (advanced use only) |
| `dry_run` | bool | no | `false` | Compute and display upgrade path without executing |

## Rollback Capability

**FULL** — A complete GitLab backup is taken before the first upgrade hop. Rollback restores all data (repositories, database, uploads, CI artifacts, secrets) to the exact pre-upgrade state. Rollback time is proportional to data volume; the operator is informed of estimated backup size during the plan phase. Any data written to GitLab between backup completion and rollback initiation will be lost — this window and its implications are surfaced at approval time.

## Smoke Test Requirements

A GitLab CE omnibus instance on EC2, pre-installed at version 16.11 using an AMI snapshot (AMI cache key: `/nexplane/smoke-amis/gitlab/16.11`). The smoke test upgrades through 16.11 → 17.0 → 17.3, verifying health checks at each hop, then exercises the full backup/restore rollback cycle and confirms the instance returns to 16.11 with data intact. The GitLab AMI cache is required because omnibus installation from scratch exceeds 60s.

## Key Risks

- **Version skip causing migration failure** — GitLab's Rails migrations assume sequential schema evolution; skipping a required stop version leaves the database in a state the new version's migrations cannot handle, causing corrupt data or failed startup. The CR mitigates this by computing the full hop sequence upfront, surfacing it for operator review at approval, and refusing to execute if the computed path is incomplete or if any intermediate package version is unavailable.
- **Sidekiq data loss between hops** — Jobs in flight when Sidekiq is drained may be lost if the worker crashes mid-hop. The CR mitigates this with an explicit queue-empty check before the package upgrade step and a configurable drain timeout; if the timeout expires with jobs remaining, the hop halts and the operator is prompted to decide.
- **Backup size and restore time** — GitLab backups include all Git repository data and can be very large; restore can take hours on large instances. The CR surfaces backup size and estimated restore time during planning so the operator can schedule the maintenance window appropriately and is not surprised by rollback duration.
