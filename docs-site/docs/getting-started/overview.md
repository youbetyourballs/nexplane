# Getting Started Overview

Nexplane is designed to be operational within minutes on a single machine using Docker Compose, and deployable to production on any Linux host that can run containers.

## Core concepts

**Connector** — An integration with a target system (AWS, GCP, Vault, LDAP, etc.). Each connector holds encrypted credentials and knows how to execute and roll back a defined set of change types.

**Change Type** — A named, parameterized operation that a connector can perform. Examples: `disable_iam_user`, `rotate_secret`, `block_s3_public_access`. Each change type defines required inputs, execution logic, and rollback logic.

**Change Request (CR)** — An instance of a change type with specific parameter values targeting a specific connector account. CRs move through states: `draft → pending_approval → approved → executing → completed` (or `failed` / `rolled_back`).

**Account** — A set of credentials for one instance of a connector (e.g. your production AWS account). A single connector type can have multiple accounts.

**Rollback** — Every completed CR stores a before-state snapshot captured during execution. Triggering rollback replays the inverse operation using that snapshot.

## Setup path

1. [Quick Start](quickstart.md) — spin up Nexplane locally with Docker Compose
2. [Connecting Your First Account](first-connector.md) — add credentials for a connector
3. [Your First Change Request](first-change-request.md) — create, approve, and execute a CR
4. [Using Rollback](rollback.md) — reverse a completed change
