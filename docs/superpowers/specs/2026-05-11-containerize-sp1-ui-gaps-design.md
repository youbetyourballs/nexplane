# SP1 — Containerization UI Gaps + Live Wizard Design

## Goal
Wire the existing containerization wizard to real application discovery and make discovered applications directly eligible for containerization from the AssetDetail view.

## Architecture

### Gap 1: Discover API Endpoint
Add `POST /assets/{id}/discover-applications` which fires an `agent_appdiscovery` CR on the asset, waits for completion (or returns CR id for polling), and returns the discovered applications list. The response shape matches what ContainerizationWizard Step 1 currently expects from `/assets/{assetId}/containerize/discover`.

### Gap 2: AssetDetail — Applications Section
When a server asset has an agent registered (has `asset_metadata.applications[]` or the asset tags include `nexplane-agent`):
- Show an "Applications" tab/section in AssetDetail
- List each discovered application with: name, binary, ports, stateful badge, containerization_status
- "Discover Applications" button fires the discover endpoint and refreshes
- Each app row has a "Containerize" button that opens the ContainerizationWizard pre-seeded with that app

### Gap 3: ContainerizationWizard — Real Step 1
Step 1 currently calls `/assets/{assetId}/containerize/discover`. Change to call the new `POST /assets/{id}/discover-applications` endpoint. CR id is returned and polled until complete. Applications from `asset_metadata.applications[]` are displayed.

### Gap 4: Smoke Test Step 4 Health Check
Step 4 polls the deployed workload's health. The `kubernetes_workload` asset has `asset_metadata.service_ip`. Poll `GET http://{service_ip}:{port}/health` with a 30s timeout and 5 retries. Show green/red per app.

### Gap 5: Kubernetes Connector Registration
When `workload_deploy` completes, the `kubernetes_cluster` asset needs to be queryable. Add a simple `kubernetes_connector_create` change type that takes `kubeconfig` + `endpoint` and registers a kubernetes connector. This is needed for SP2 (EKS provisioning) to auto-register the cluster.

## Data Flow

```
AssetDetail (server asset with nexplane-agent)
  → "Applications" tab
  → "Discover Applications" button
  → POST /assets/{id}/discover-applications
  → fires agent_appdiscovery CR
  → polls CR until completed
  → writes to asset_metadata.applications[]
  → tab refreshes showing discovered apps

Per-app "Containerize" button
  → opens ContainerizationWizard
  → Step 1: shows pre-discovered apps
  → Step 2: build (agent_containerize_build CR)
  → Step 3: deploy (k8s_workload_deploy CR, needs cluster asset)
  → Step 4: health check polling
  → Step 5: retire (agent_containerize_retire CR)
```

## Components Changed
- `backend/app/routers/assets.py` — add POST /discover-applications endpoint
- `backend/app/routers/change_requests.py` — `/confirm-stateful` endpoint (already referenced in executor)
- `frontend/src/pages/AssetDetail.tsx` — add Applications section
- `frontend/src/components/ContainerizationWizard.tsx` — wire Step 1 to real endpoint
- `frontend/src/components/ContainerizeWizardStep4.tsx` — real health check polling
- `backend/app/connectors/change_type_definitions/kubernetes_connector_create.json` — new
- `backend/app/connectors/executors/kubernetes/create_connector.py` — new

## Smoke Test Coverage
- Phase CONTAINER-A: install payments app on EC2, discover apps, verify asset_metadata.applications[] populated
- Phase CONTAINER-B: build (dry_run=True), verify CR completes and dockerfile in metadata
- Phase CONTAINER-C: deploy to a registered k8s cluster, verify kubernetes_workload asset created
- Phase CONTAINER-D: retire, verify containerization_status="retired"

## Error Handling
- If no agent registered for asset: return 409 with "Deploy Nexplane agent first"
- If agent job times out: CR fails with clear message
- If Docker not available on EC2: build CR fails; user sees "Docker not installed on host"
- If cluster unreachable: deploy CR fails with kubectl error surfaced
