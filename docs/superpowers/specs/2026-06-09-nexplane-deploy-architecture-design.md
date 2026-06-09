# Nexplane Deploy — Architecture Design

> **For agentic workers:** This is an umbrella architecture spec covering five sub-projects. Each sub-project gets its own implementation plan. Implement sub-projects in order: 1 → (2 and 3 in parallel) → 4 → 5.

**Goal:** Build a commercial deployment harness for provisioning and managing independent Nexplane instances for clients, with first-use credential flow, external identity provider support, and multi-format self-hosted delivery — all using Nexplane itself as the control plane.

**Architecture:** A new private `nexplane-deploy` repo acts as a commercial overlay. A dedicated "ops" Nexplane instance mounts commercial CR catalog and executors from that repo and uses them to provision, seed, and manage client instances. The `nexplane` core repo gains first-use token flow and IdP support but contains no commercial code.

**Delivery models:**
- **Managed:** Nexplane provisions and operates one EC2 per client in Nexplane's AWS account, fully isolated
- **Self-hosted:** Customer deploys in their own infrastructure using a Docker Compose bundle, VM image, or Helm chart

---

## Sub-project 1: `nexplane-deploy` Repo + Bootstrap

### Repo structure

New private GitHub repo `nexplane-deploy`:

```
nexplane-deploy/
├── bootstrap/          # Terraform: ops instance + CI test ops instance
├── catalog/            # Commercial CR catalog JSONs (mounted into ops instance)
├── executors/          # Commercial CR Python executors
├── client-registry/    # Per-client YAML configs (git-tracked, one file per client)
├── smoke/              # Live smoke tests for commercial CRs (same pattern as test_aws_live.py)
├── artifacts/          # CI pipelines: Docker Compose bundle, VM image builds, Helm packaging
└── demo-seeds/         # SQL/fixture files for demo mode pre-population
```

### Ops instance

A standard `nexplane` instance running in Nexplane's AWS account with two extra environment variables:

- `NEXPLANE_EDITION=commercial` — enables first-use token API and trial seeding endpoints
- `NEXPLANE_COMMERCIAL_CATALOG_PATH=/mnt/commercial/catalog` — path where `catalog/` and `executors/` from `nexplane-deploy` are mounted via Docker volume

The commercial catalog is mounted at runtime; no commercial code lives in the `nexplane` image.

A second identical instance runs as the CI test ops instance, used exclusively for smoke testing commercial CRs.

### Bootstrap

`bootstrap/` contains Terraform modules that provision:
- The ops Nexplane instance (EC2, security groups, IAM role, EBS volume)
- The CI test ops instance (identical spec, separate state)
- S3 bucket policy additions for artifact publishing
- SSM parameters for ops instance config

---

## Sub-project 2: Commercial CR Types

Seven CR types defined in `catalog/` and implemented in `executors/`. Available only on instances where `NEXPLANE_COMMERCIAL_CATALOG_PATH` is set. All follow the standard plan → approve → execute → rollback lifecycle and appear in the ops instance's CR catalog.

### `provision_instance`

**Managed mode:** provisions a client EC2 in Nexplane's AWS account, installs Docker, pulls the Nexplane image, writes `.env` from a template sourced from the client registry YAML, starts the stack via `docker-compose.prod.yml`. Outputs: instance ID, private IP, Tailscale IP, instance URL. Registers the instance as an asset in the ops instance inventory.

**Self-hosted mode:** generates a presigned S3 URL (72h TTL) for the appropriate delivery artifact, pre-fills startup parameters from the client registry entry, generates the setup token, and produces an onboarding package (pre-filled bootstrap invocation or templated onboarding email). No EC2 is provisioned by Nexplane.

**Rollback:** terminate the EC2 (managed) or revoke the presigned URL and setup token (self-hosted). Remove client registry entry.

### `generate_setup_token`

Creates a short-lived (24h) one-time setup token in the target instance's DB, scoped to that instance URL. Outputs the setup URL. Rollback: revoke the token. Called automatically as part of `provision_instance` but can be run independently to re-send a setup link.

### `seed_trial`

Runs against a freshly provisioned instance in one of two modes:

- `demo` — inserts a curated set of CRs, connectors, and assets from `demo-seeds/` fixtures so a prospect can explore immediately without configuring anything
- `fresh` — no-op seeding; verifies instance health and readiness for real customer onboarding

### `terminate_instance`

Tears down a managed client EC2, removes the asset from ops inventory, removes the client registry entry, revokes any active tokens. Also the rollback action for `provision_instance` in managed mode.

### `rotate_instance_credentials`

Rotates `SECRET_KEY` and DB password on a running client instance. Follows the reconstitution pattern: snapshots current credentials before rotating, restores on failure. Used for scheduled key rotation.

### `upgrade_instance`

Pulls the latest (or a specified) image tag on a client instance, performs a rolling restart. Rollback: pulls the previous tag and restarts.

### `reset_instance_auth`

Resets a client instance's auth mode back to local, generates a recovery token for the admin account, and optionally re-enables a specific local account. Requires a support ticket reference in `desired_outcome` for audit trail. Calls a gated endpoint on the target instance that only accepts requests signed by the ops instance's identity. Used by Nexplane support after a ticket is filed and verified.

---

## Sub-project 3: Core Auth Changes (`nexplane` repo)

### First-use token flow

New `setup_tokens` table: `id`, `token_hash`, `instance_url`, `expires_at` (24h), `used_at`, `org_id`.

On a freshly provisioned instance, all routes redirect to `/setup` until a valid token is consumed. The setup page collects: admin email, password, organization name. On submit: token is marked used, admin account created, org initialized, instance unlocks. Tokens are single-use and cannot be replayed.

Gated behind `NEXPLANE_EDITION=commercial`. Self-hosted instances without this flag use the existing seeded admin account from `docker-compose.prod.yml`.

A separate gated endpoint (called by `reset_instance_auth` CR) accepts a signed request from the ops instance to reset auth mode and generate a new recovery token.

### Identity providers

New `identity_providers` table:

| column | type | notes |
|---|---|---|
| `id` | UUID | |
| `org_id` | UUID FK | |
| `type` | enum | `oidc`, `ldap`, `saml` |
| `name` | text | display name on login page |
| `status` | enum | `pending`, `active` |
| `enabled` | bool | |
| `config` | JSONB | type-specific fields |
| `connector_id` | UUID FK nullable | reference to existing connector for connection details |

New router at `/identity-providers` with CRUD endpoints.

**OIDC config:** `issuer`, `client_id`, `client_secret`, `scopes`, `icon`. Login redirects to issuer's authorization endpoint; callback exchanges code for user info; matches on email to existing account or creates one if `auto_provision` is true.

**LDAP config:** `base_dn`, `user_attr`, `group_attr`. If `connector_id` is set, `host`/`port`/`tls_cert` are read from the referenced connector record — not duplicated. Login binds with the user's own credentials against the LDAP server.

**SAML config:** `metadata_url`, `entity_id`, `attribute_mappings`. SP metadata auto-generated at `/auth/saml/{idp_id}/metadata`.

### Auth mode

Org-level switch: `local` (default) or `idp`. Not both simultaneously.

An IdP is configured and tested while the org is still in `local` mode (IdP status = `pending`). When ready, admin explicitly activates via a "switch to IdP" action — flips org auth mode and sets IdP status to `active` in one atomic step. Existing local accounts are retained and matched by email on first IdP login so account history, roles, and permissions carry over.

Rollback: admin can switch back to local within a 24h grace period. After that, recovery requires `reset_instance_auth` CR from the ops instance.

### Import from connector UX

When creating an LDAP or OIDC IdP in the UI, if a matching connector exists in the org, a banner offers to pre-fill overlapping connection fields. Selecting it sets `connector_id` and populates the form. The link is live — changes to the referenced connector propagate to the IdP config.

### Login page

Renders an "or continue with" section listing enabled IdPs for the org, sourced from `/identity-providers`. Only shown when org auth mode is `idp` and at least one IdP is active.

---

## Sub-project 4: Extended IdP Support (LDAP/AD + SAML)

Follows the same `identity_providers` model from sub-project 3. Implemented after OIDC is stable.

**LDAP/AD:** user search against `base_dn`, configurable `user_attr` (default `uid` for OpenLDAP, `sAMAccountName` for AD), optional group sync to Nexplane roles via `group_attr` mapping. If an AD connector exists in the org, `connector_id` reference pre-fills host/port/certs.

**SAML:** standard SP-initiated flow. Nexplane registers as SP using metadata at `/auth/saml/{idp_id}/metadata`. Supports attribute mapping for email, name, and role. Works with any compliant IdP (Okta, Azure AD, Google Workspace, ADFS).

---

## Sub-project 5: Delivery Artifacts

CI pipelines in `nexplane-deploy/artifacts/` producing three self-hosted formats on every release tag. All artifacts published to the same S3 bucket as agent binaries.

### S3 layout

```
s3://nexplane-dist/
├── agents/                          # existing agent binaries (unchanged)
├── bundles/
│   ├── v0.1.2/
│   │   ├── nexplane-v0.1.2-compose.tar.gz
│   │   ├── nexplane-v0.1.2.ova
│   │   ├── nexplane-v0.1.2.vmdk
│   │   └── checksums.sha256
│   └── latest -> v0.1.2/
└── helm/                            # Helm chart tarballs (mirror of GHCR OCI registry)
```

### Docker Compose bundle

Versioned tarball: `docker-compose.prod.yml`, `.env.template`, `bootstrap.sh`, `VERSION`. The bootstrap script validates Docker version, prompts for or accepts env parameters (org name, admin email, network config), writes `.env`, pulls images, and starts the stack. On first run it generates a local setup token and prints the setup URL. Built by GitHub Actions on release tag, pushed to S3.

### VM image

Packer build in `artifacts/vm/` baking Docker, Docker Compose, Nexplane images, and `bootstrap.sh` into:
- **OVA** — VMware/VirtualBox
- **VMDK** — Hyper-V
- **AMI** — for customers running their own AWS accounts

First boot runs `bootstrap.sh` automatically via cloud-init / VM startup script. Built on release tags, stored in S3 alongside the compose bundle.

### Helm chart

Existing `helm/nexplane/` chart in the `nexplane` repo is packaged and pushed to a private Helm OCI registry on GHCR on release. `artifacts/helm/` holds only the release pipeline — chart source stays in `nexplane`.

### Onboarding package generation

`provision_instance` in self-hosted mode generates a presigned S3 URL (72h TTL) scoped to the specific artifact version and client. The onboarding package includes: the presigned URL, pre-filled `bootstrap.sh` invocation with all startup parameters, the setup URL, and a how-to covering network prerequisites (firewall rules, DNS, Tailscale if used).

---

## Smoke Coverage

`nexplane-deploy/smoke/` contains live smoke tests following the same pattern as `test_aws_live.py`. A CI test ops Nexplane instance runs commercial CRs against real infrastructure. Phases:

| Phase | What it tests |
|---|---|
| `PROVISION_INSTANCE` | Spin up real EC2, verify reachable, verify setup token works, first login |
| `TRIAL_DEMO_SEED` | Demo mode populates expected CRs, connectors, assets |
| `TRIAL_FRESH` | Fresh mode starts empty, instance healthy |
| `TERMINATE_INSTANCE` | Cleanup, asset removed from inventory, rollback path |
| `ROTATE_INSTANCE_CREDENTIALS` | SECRET_KEY + DB password rotation, reconstitution on failure |
| `UPGRADE_INSTANCE` | Image upgrade + rollback to previous tag |
| `RESET_INSTANCE_AUTH` | Auth reset, recovery token, re-enable local account |
| `ARTIFACT_DELIVERY` | Compose bundle: download, bootstrap, setup token, first login |
| `IDP_OIDC` | OIDC login flow end-to-end (generic OIDC provider) |
| `IDP_LDAP` | LDAP login against DC smoke AMI |

No mocks. Every phase exercises real infrastructure and verifies rollback.

---

## Implementation Order

1. **Sub-project 1** — `nexplane-deploy` repo skeleton + Terraform bootstrap (unblocks everything)
2. **Sub-project 2 + 3 in parallel** — Commercial CRs + Core auth changes
3. **Sub-project 4** — Extended IdP (LDAP/AD + SAML), after OIDC stable
4. **Sub-project 5** — Delivery artifacts, after provision flow proven
