# Architecture

Nexplane has a two-tier architecture: a **control plane** that you operate, and an optional **agent** that operators can deploy on their own infrastructure for host-level operations.

## Control plane

The control plane consists of:

- **Backend API** — A Python (FastAPI/Django) application that handles authentication, change request lifecycle, connector credential management, and execution orchestration. Runs on port 8000.
- **Frontend** — A React single-page application that provides the user interface for creating, approving, and monitoring change requests. Runs on port 3000 (development) or port 80 (production).
- **Database** — PostgreSQL stores all change requests, audit logs, connector accounts (credentials encrypted), and user data. SQLite is supported for local evaluation only.
- **Connector executors** — Python modules loaded by the backend that implement the execution and rollback logic for each connector type. Executors run within the backend process and call out to external APIs (AWS, GCP, Vault, etc.) directly.

## Agent (optional)

The agent is a lightweight process deployed by the operator on infrastructure they manage (e.g. a Linux server or Windows host). It is used for operations that require local host access, such as:

- Installing software
- Managing local user accounts
- Executing allowlisted shell or PowerShell commands

The agent **phones home** to the control plane over outbound HTTPS. The control plane never initiates inbound connections to the agent, which means agents can run behind NAT or in private networks without firewall rule changes.

```
┌─────────────────────────────────────────┐
│              Control Plane              │
│                                         │
│  ┌──────────┐      ┌──────────────────┐ │
│  │ Frontend │─────▶│   Backend API    │ │
│  └──────────┘      └──────┬───────────┘ │
│                           │             │
│                    ┌──────▼───────────┐ │
│                    │ Connector        │ │
│                    │ Executors        │ │
│                    └──────┬───────────┘ │
└───────────────────────────┼─────────────┘
                            │
              ┌─────────────┼──────────────────┐
              │             │                  │
         HTTPS API     HTTPS API          HTTPS API
              │             │                  │
         ┌────▼───┐   ┌─────▼────┐   ┌────────▼──────┐
         │  AWS   │   │  Vault   │   │ Agent (Linux/ │
         │  GCP   │   │  LDAP    │   │  Windows host)│
         │  Azure │   │  etc.    │   └───────────────┘
         └────────┘   └──────────┘
```

## Data flow

1. **User submits CR** — The frontend sends a CR creation request to the backend API. The backend validates the parameters against the change type schema and stores the CR with status `pending_approval`.
2. **Approval** — An authorized reviewer approves the CR via the UI or API. The CR status advances to `approved`.
3. **Execution** — The backend routes the CR to the appropriate connector executor. The executor authenticates to the target system, captures the before-state snapshot, applies the change, and stores the result.
4. **Result stored** — The execution result (success or failure), snapshot data, timestamps, and executing user are written to the CR record.
5. **Rollback available** — If the executor completed successfully, a rollback is available. The snapshot is retained in the CR record indefinitely.

## Deployment modes

| Mode | Description |
|---|---|
| Single-node Docker Compose | All components on one host; suitable for evaluation and small teams |
| Docker Compose with external Postgres | Backend + frontend on one host, managed database |
| Kubernetes (Helm) | Production-grade deployment with horizontal scaling — See [Helm chart](https://github.com/nexplane/nexplane/tree/master/helm/nexplane) for installation |
