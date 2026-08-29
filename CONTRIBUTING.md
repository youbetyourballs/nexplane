# Contributing to Nexplane

Nexplane is a security change management platform — an OS for infrastructure intent. It lets operators express what they want done to their infrastructure and delegates execution with a guaranteed rollback path. Contributing here means you are working on software that runs inside production security environments. Correctness and live verification are not optional.

## Before You Start

### CLA

External contributors must sign a Contributor License Agreement before any pull request can be merged. Contact [john.o.terrill@gmail.com](mailto:john.o.terrill@gmail.com) to receive the CLA. This is a hard requirement — PRs from contributors without a signed CLA will not be reviewed.

### Code of Conduct

Nexplane uses the [Contributor Covenant](https://www.contributor-covenant.org/version/2/1/code_of_conduct/) as its code of conduct. Be direct and professional.

## Dev Setup

**Prerequisites**

- Docker and Docker Compose
- Go 1.22+
- Python 3.12+
- Node 20+

**Steps**

```bash
git clone https://github.com/youbetyourballs/nexplane.git
cd nexplane
cp .env.example .env   # fill in required values
docker compose up
```

- Frontend: http://localhost:5173
- Backend API: http://localhost:8000
- API docs: http://localhost:8000/docs

The containers mount the local `backend/` and `frontend/src/` directories, so backend code changes are picked up automatically. Frontend changes require a container restart:

```bash
docker compose stop frontend && docker compose up frontend -d
```

## Agent Development

The agent is a Go binary. Build it from the `agent/` directory:

```bash
cd agent
make build VERSION=1.2.3
```

`VERSION` must match or exceed the server's expected agent version. If it is lower, the platform's auto-update mechanism will replace your local binary with the published release. Check the server's configured minimum agent version before building.

## Code Standards

### SPDX License Headers

Every new source file must include an SPDX header as the first line:

Python / shell:
```python
# SPDX-License-Identifier: AGPL-3.0-only
```

Go / TypeScript / JavaScript:
```go
// SPDX-License-Identifier: AGPL-3.0-only
```

PRs that add files without this header will not be merged.

### No Mocks for Connector or Executor Tests

Connector and executor logic must be tested against real infrastructure. Mock-based tests for this layer are not acceptable — they do not verify the thing that matters (that the executor does what it claims against a real system). Unit tests for pure logic (parsing, data transformation, schema validation) are fine and encouraged. The rule applies specifically to anything that touches an external system.

### Smoke Tests Are the Bar for "Done"

Every new executor must ship with a passing smoke test before the PR is considered complete. A smoke test is a live end-to-end run: create CR, plan, approve, execute, verify the effect on real infrastructure, rollback, verify the rollback. Green unit tests without a passing smoke test means the work is unfinished.

## Testing

### Backend Unit Tests

```bash
cd backend
pytest tests/
```

### Agent Tests

```bash
cd agent
go test ./...
```

### Smoke Tests

Smoke tests run against live infrastructure and require environment variables:

| Variable | Description |
|---|---|
| `API_URL` | Base URL of the running Nexplane API |
| `API_TOKEN` | Valid API token with executor permissions |
| `AGENT_ASSET_ID` | Asset ID of the agent registered with the platform |

Additional variables depend on the connector being tested (e.g., AWS credentials, LDAP endpoint, Vault token). Check the smoke test file for the connector you are working on — required vars are declared at the top.

Smoke tests are located in `backend/tests/smoke/`. Run a specific phase:

```bash
cd backend
pytest tests/smoke/test_<connector>_smoke.py -v
```

Smoke tests must run from a machine that has network access to the target infrastructure. Running them from a local laptop against VPC-internal resources will fail. Use an EC2 instance in the same VPC or connect via Tailscale.

## Pull Request Process

A good PR:

- Has a clear description of what changed and why
- Includes the smoke test for any new executor, with evidence of a passing run (paste the output or link to a CI run)
- Sets `smoke_verified: true` in the catalog entry for any CR type whose smoke test has passed
- Adds SPDX headers to every new file
- Does not mix unrelated changes — one topic per PR
- Passes `pytest tests/` and `go test ./...` before submission

PRs that add a new connector or CR type without a passing smoke test will be sent back. This is not negotiable — the rollback guarantee only holds if it has been verified live.

## Questions

Open a [GitHub Issue](https://github.com/youbetyourballs/nexplane/issues) for bugs, questions about the codebase, or proposals for new features. For CLA or licensing questions, email [john.o.terrill@gmail.com](mailto:john.o.terrill@gmail.com).
