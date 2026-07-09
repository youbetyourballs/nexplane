# Platform Image Publishing Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** On every `git tag v*.*.*`, produce a public EC2 AMI (us-east-1) with the full Nexplane platform pre-installed and running on boot — zero-config, static default credentials, accessible at `http://<public-ip>/` on port 80. The AMI ID is published to `releases.nexplane.ai/latest.json` only after a smoke test verifies the image works.

**Architecture:** Packer runs in the `release.yml` CI pipeline after Docker images are pushed to GHCR. It launches a base Ubuntu 22.04 instance, installs Docker + docker-compose + nginx, builds a production frontend image from source (with a same-origin API URL), pulls the versioned backend image from GHCR, writes a production `docker-compose.ami.yml` and systemd unit, snapshots the AMI, and makes it public. A post-build smoke test launches a test instance, verifies HTTP + auth + container health, then tears down before the manifest is updated.

**Tech Stack:** HashiCorp Packer (HCL2 template), GitHub Actions, AWS EC2 / AMI APIs, Python smoke script, nginx (Docker container), systemd, docker-compose v2.

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

### `releases.nexplane.ai/latest.json` — new fields
```json
{
  "version": "v1.2.3",
  "ami_id": "ami-0abc123def456789",
  "ami_region": "us-east-1",
  "ami_launch_url": "https://console.aws.amazon.com/ec2/v2/home?region=us-east-1#LaunchInstanceWizard:ami=ami-0abc123def456789"
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

## Success Criteria

- `build-ami` PASSED: Packer completes, AMI ID captured, AMI is in `available` state
- `smoke-ami` PASSED: all 5 HTTP/SSH assertions green, test instance terminated
- `publish-manifest` PASSED: AMI is public, `latest.json` contains `ami_id` + `ami_launch_url`
- Full pipeline: tag pushed → AMI live and launchable within ~20 minutes
