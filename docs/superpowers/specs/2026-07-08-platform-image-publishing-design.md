# Platform Image Publishing Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** On every `git tag v*.*.*`, produce a public EC2 AMI (us-east-1) with the full Nexplane platform pre-installed and running on boot — zero-config, static default credentials, accessible at `http://<public-ip>/` on port 80. The AMI ID is published to `releases.nexplane.ai/latest.json` only after a smoke test verifies the image works.

**Architecture:** Packer runs in the `release.yml` CI pipeline after Docker images are pushed to GHCR. It launches a base Ubuntu 22.04 instance, installs Docker + docker-compose + nginx, builds a production frontend image from source (with a same-origin API URL), pulls the versioned backend image from GHCR, writes a production `docker-compose.ami.yml` and systemd unit, snapshots the AMI, and makes it public. A post-build smoke test launches a test instance, verifies HTTP + auth + container health, then tears down before the manifest is updated.

**Tech Stack:** HashiCorp Packer (HCL2 template), GitHub Actions, AWS EC2 / AMI APIs, Python smoke script, nginx (Docker container), systemd, docker-compose v2.

**Future formats (out of scope for this plan, architecture must not preclude):**
- **OVA** — on-premise VMware/VirtualBox deployments; same Packer template, different builder
- **Azure VHD / GCP image** — other cloud providers; same provisioner scripts, different Packer builder + publish step
- **Docker Compose bundle** — tarball with images + compose file for self-hosted Docker installs

The `packer/` directory and provisioner scripts should be structured so adding a new builder is additive (new `.pkr.hcl` file + publish job) without modifying the existing AMI path.

---

## Global Constraints

- AMI is public, us-east-1 only, on every `git tag v*.*.*`
- AMI ID is link-distributed only — not advertised on the public marketing site
- Zero config on first launch: no user-supplied env vars, credentials, or config files required
- Default credentials: `admin@nexplane.local` / `changeme` — printed in `/etc/motd` on the instance
- All platform services accessible at `http://<public-ip>/` (port 80) via nginx reverse proxy
- Smoke test must pass before AMI is made public or `latest.json` is updated — pipeline fails if smoke fails
- No Tailscale in the AMI (removed from the production compose; users connect directly via public IP)
- No `--reload` uvicorn flag in AMI (production mode)
- Source code is NOT present on the AMI filesystem; only pre-built images

---

## Components

### `packer/nexplane.pkr.hcl`
Packer HCL2 template. Launches a `t3.medium` Ubuntu 22.04 base instance, runs provisioner scripts, creates AMI named `nexplane-{version}`, tags it `public = "true"`.

### `packer/scripts/setup.sh`
Provisioner script that runs inside the Packer instance:
1. Install Docker CE, docker-compose v2 plugin, Python3
2. Log in to GHCR (using build-time `GHCR_TOKEN` variable)
3. Pull `ghcr.io/youbetyourballs/nexplane-backend:{version}`
4. Build production frontend image from source checkout with `VITE_API_URL=` (empty — same-origin calls via nginx)
5. Write `/opt/nexplane/docker-compose.ami.yml` (production compose — see below)
6. Write `/etc/motd` with default credentials and launch instructions
7. Install and enable `nexplane.service` systemd unit

### `packer/scripts/nexplane.service`
Systemd unit that runs `docker compose -f /opt/nexplane/docker-compose.ami.yml up -d` on boot (after `docker.service`).

### `packer/docker-compose.ami.yml`
Production compose file written to `/opt/nexplane/docker-compose.ami.yml` on the AMI:
- **db** — `postgres:16-alpine`, volume `nexplane_data:/var/lib/postgresql/data`, static credentials
- **backend** — `ghcr.io/youbetyourballs/nexplane-backend:{version}`, production env vars, no source volume mount, uvicorn without `--reload`, runs `alembic upgrade heads && python seed.py` on start
- **frontend** — locally built production image (built during Packer run), no source volume mount
- **nginx** — `nginx:alpine`, port 80 exposed; routes `/` to frontend:3000, `/api/` to backend:8000 (strips `/api` prefix)
- **nexplane-watchdog** — `ghcr.io/youbetyourballs/nexplane-watchdog:{version}` if published, else omitted for v1
- No mailhog, no tailscale

Production env vars baked into compose:
```
DATABASE_URL: postgresql+asyncpg://nexplane:nexplane_prod@db:5432/nexplane
SECRET_KEY: nexplane-default-secret-key-change-before-production
ENVIRONMENT: production
CORS_ORIGINS: "*"
DEMO_MODE: "true"
```

### `packer/smoke_ami.py`
Python smoke script run from CI after AMI build. Accepts `--ami-id`, `--key-name`, `--security-group-id`. Performs:
1. Launch test instance from AMI (`t3.medium`, us-east-1)
2. Poll EC2 until instance status checks pass (up to 5 min)
3. Poll `GET http://<public-ip>/` until HTTP 200 (up to 3 min, 10s interval)
4. `POST http://<public-ip>/api/auth/login` with default creds → assert token returned
5. `GET http://<public-ip>/api/assets` with token → assert HTTP 200
6. SSH to instance: run `docker ps --format '{{.Names}}'` → assert all 4 expected containers present
7. Terminate instance and wait for termination
8. Exit 0 on pass, exit 1 on any failure (CI fails the pipeline)

### `.github/workflows/release.yml` — additions
Three new jobs added after `build-and-release`:

**`build-ami`** (depends on `build-and-release`):
- Checkout repo
- Install Packer
- Run `packer build` with version and GHCR token passed as variables
- Output: `ami_id` (captured from Packer stdout via `packer build -machine-readable`)

**`smoke-ami`** (depends on `build-ami`):
- Configure AWS credentials
- Create a temporary security group allowing inbound 22 + 80 from the runner IP
- Upload a temporary SSH key pair
- Run `python packer/smoke_ami.py --ami-id ${{ needs.build-ami.outputs.ami_id }} ...`
- Always-run cleanup: delete temp key pair and security group regardless of smoke outcome
- If smoke fails: pipeline stops here; AMI remains private (not published)

**`publish-manifest`** (depends on `smoke-ami`):
- Make AMI public: `aws ec2 modify-image-attribute --image-id <ami_id> --launch-permission Add=[{Group=all}]`
- Download current `releases.nexplane.ai/latest.json` from S3
- Merge new fields: `ami_id`, `ami_region: "us-east-1"`, `ami_launch_url`
- Upload updated `latest.json` to S3 + invalidate CloudFront

### `packer/templates/quickstart.cfn.yml.tmpl`
CloudFormation template with `AMI_ID` literal placeholder. Creates a security group (ports 80 + 22), launches a `t3.medium` instance, and outputs `PlatformURL`, `SSHCommand`, and `PublicIP`. Rendered by `publish-manifest` CI job and uploaded to `releases.nexplane.ai/launch/quickstart.cfn.yml`. The CF stack console deep link in `LAUNCH.md` points directly to this file so users click once and the template is pre-loaded.

### `packer/templates/LAUNCH.md.tmpl`
Step-by-step launch instructions with `AMI_ID` placeholder. Three paths: (A) one-click CloudFormation (recommended), (B) AWS console manual, (C) CLI one-liner. Includes SSH access commands, docker compose status/logs commands, and a credentials table. Rendered and uploaded to `releases.nexplane.ai/launch/LAUNCH.md` on every release.

### `releases.nexplane.ai/latest.json` — new fields
```json
{
  "version": "v1.2.3",
  "ami_id": "ami-0abc123def456789",
  "ami_region": "us-east-1",
  "ami_launch_url": "https://console.aws.amazon.com/ec2/v2/home?region=us-east-1#LaunchInstanceWizard:ami=ami-0abc123def456789",
  "launch_instructions_url": "https://releases.nexplane.ai/launch/LAUNCH.md",
  "cloudformation_quickstart_url": "https://releases.nexplane.ai/launch/quickstart.cfn.yml"
}
```

---

## Security Considerations

- GHCR token used during Packer build is a build-time variable only; not baked into the AMI
- The static `SECRET_KEY` in the AMI compose is documented as a known default — appropriate for a demo/eval AMI; nexplane-deploy handles real key injection for client instances
- The AMI is public but link-distributed; AMI ID not published on the marketing site
- SSH key used by smoke test is ephemeral (created and deleted per pipeline run)
- Temp security group is always cleaned up in an `if: always()` step

---

## Frontend Same-Origin Architecture

The existing GHCR frontend images have `VITE_API_URL` baked in at build time. The AMI build therefore builds a fresh production frontend image from source with `VITE_API_URL=` (empty string). With an empty VITE_API_URL, Axios/fetch calls use a relative path (`/api/...`). Nginx on port 80 routes:

```nginx
location /api/ {
    proxy_pass http://backend:8000/;
}
location / {
    proxy_pass http://frontend:3000/;
}
```

This means the frontend and API are both accessible from the same public IP on port 80, with no hardcoded IP or domain needed.

---

## Boot Sequence

1. Instance boots from AMI
2. `nexplane.service` systemd unit fires: `docker compose -f /opt/nexplane/docker-compose.ami.yml up -d`
3. `db` starts first (healthcheck)
4. `backend` starts: runs `alembic upgrade heads` → `python seed.py` → uvicorn (production mode)
5. `frontend` and `nginx` start once backend is healthy
6. Platform accessible at `http://<public-ip>/` within ~90 seconds of launch
7. `/etc/motd` shown on SSH: default credentials + access URL

---

## Smoke Test Phases

`packer/smoke_ami.py` runs 8 sequential phases against the live test instance. All phases must pass before the AMI is published.

| Phase | What is verified |
|-------|-----------------|
| **1. Launch + network** | Instance launches from AMI; EC2 status checks pass; public IP assigned; port 80 responds HTTP 200 within boot timeout |
| **2. Container health** | SSH `docker ps` confirms all expected containers (`db`, `backend`, `webserver`) are in `running` state |
| **3. Authentication** | `POST /api/auth/login` with default creds returns JWT; `GET /api/auth/me` returns admin profile; token works for subsequent requests |
| **4. Asset management** | Create 3 assets (web/app/db server); create `depends_on` chain; `GET /api/impact-simulation` returns correct upstream/downstream for each node; verify `downstream_risk.total >= 2` from root |
| **5. Connector configuration** | `POST /api/connectors` creates AWS connector (fake creds — tests config API, not connectivity); `PUT /api/connectors/{id}/credentials` accepts credentials; `GET /api/connectors` lists it; create a second connector (agent_tunnel type) |
| **6. Change request lifecycle** | Create CR targeting mid asset; `POST /plan` → poll until `planned`; verify `change_plan.blast_radius` populated with downstream count; `POST /submit-for-approval`; `POST /approve` with `decision=approved`; `POST /execute` → accept `executing`, `failed`, or `completed` as valid (fake creds will fail execution — that is expected and verified as graceful, not a crash) |
| **7. Rollback** | `POST /change-requests/{id}/rollback` or `POST /project-rollbacks` → assert API returns 200/201 and rollback record created; verify rollback status is queryable |
| **8. Feature surface spot-check** | `GET /api/vulnerabilities` → 200; `GET /api/compliance/baselines` → 200; `GET /api/runbooks` → 200; `GET /api/access-reviews` → 200; `GET /api/recurring-jobs` → 200; `GET /api/backup-targets` → 200; `GET /api/change-requests` → 200 and list non-empty (the CR created in phase 6) |
| **9. MCP server + agent token** | `POST /auth/agent-tokens` creates agent token; `GET /auth/agent-tokens` lists it; `GET /api/mcp` SSE endpoint returns 200; tool list parsed and verified to contain all expected tool categories (`list_change_requests`, `list_assets`, `list_findings`, `list_connectors`, `list_runbooks`, `get_fleet_context`); agent token deleted after verification |

Cleanup runs in a `finally` block regardless of outcome: DELETE created CRs, assets, connectors; terminate EC2 instance; delete ephemeral key pair and security group.

---

## Success Criteria

- `build-ami` PASSED: Packer completes, AMI ID captured, AMI is in `available` state
- `smoke-ami` PASSED: all 9 phases green, test instance terminated, ephemeral AWS resources deleted
- `publish-manifest` PASSED: AMI is public, `latest.json` contains `ami_id` + `ami_launch_url`
- Full pipeline: tag pushed → AMI live and launchable within ~25 minutes
