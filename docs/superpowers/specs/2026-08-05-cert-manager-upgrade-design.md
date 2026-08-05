# cert-manager Rolling Upgrade Design

**Goal:** Upgrade cert-manager (CRDs + controller) in a Kubernetes cluster to a target version while preserving all existing Certificate, Issuer, and ClusterIssuer resources and guaranteeing full rollback capability.

**Architecture:** The executor follows a strict CRD-first sequence: new CRDs are installed before the controller is upgraded, which is backward-compatible and ensures no Certificate or Issuer resources are destroyed. After the controller upgrade, the executor waits for webhook readiness before declaring success, avoiding a known race window where Certificate objects submitted too early will be rejected with webhook timeout errors. Rollback is additive — old manifests can be re-applied without data loss because CRD changes never delete existing objects.

## Phases

1. **Preflight** — Verify all cert-manager deployments (`cert-manager`, `cert-manager-cainjector`, `cert-manager-webhook`) have all replicas available; record current CRD versions and certificate count/Ready status across all namespaces; warn if any certificate expires within 24h (upgrade may delay renewal); validate Kubernetes API version compatibility (cert-manager 1.13+ requires K8s 1.22+); if Helm-managed, record current chart version and release values.

2. **Snapshot** — Export all Certificate, Issuer, ClusterIssuer, and CertificateRequest objects to YAML under `backup_path`; export Helm release values (`helm get values cert-manager -n cert-manager`) if Helm-managed; record current controller image digest. These files are the rollback source of truth.

3. **Upgrade** — Step 1: Install new CRDs via `kubectl apply -f cert-manager.crds.yaml` and wait for `Established` condition on all cert-manager CRDs (timeout 60s). Step 2: Upgrade the controller — either `helm upgrade` with `--reuse-values` or `kubectl apply` of the full manifest. Step 3: Wait for all three deployments to reach `Available` via `kubectl rollout status` (timeout 120s each). Step 4: Probe webhook readiness by attempting to apply a test Certificate object with retry/backoff until the webhook accepts it (max `webhook_ready_timeout_seconds`).

4. **Verify** — Confirm all pods in the cert-manager namespace are `Running`; verify the controller image tag matches `target_version`; confirm certificate count is unchanged and all are `Ready`; issue a test certificate via a self-signed ClusterIssuer and confirm it reaches `Ready` state within 60s; delete the test certificate after verification.

5. **Rollback** — Re-apply backed-up CRD YAML to restore previous schema; run `helm rollback cert-manager` or `kubectl apply` of the prior manifest; existing Certificate and Issuer objects are unaffected (objects persist through CRD schema changes — no data loss). Remove any test Certificate created during verification. Record rollback completion in FILO stack.

## CR Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `target_version` | string | yes | — | Target cert-manager version, e.g. `"1.13.3"` |
| `install_method` | string | no | `"helm"` | `"helm"` or `"kubectl"` |
| `kubeconfig_connector_id` | string | yes | — | Connector ID providing kubeconfig access |
| `namespace` | string | no | `"cert-manager"` | Namespace where cert-manager is installed |
| `helm_release_name` | string | no | `"cert-manager"` | Helm release name (Helm installs only) |
| `helm_repo_url` | string | no | `"https://charts.jetstack.io"` | Helm chart repository URL |
| `helm_values_override` | dict | no | `{}` | Values merged with existing release values on upgrade |
| `webhook_ready_timeout_seconds` | int | no | `60` | Max seconds to wait for webhook to accept objects |
| `backup_path` | string | no | `"/tmp/cert-manager-backup"` | Directory for YAML snapshots |
| `dry_run` | bool | no | `false` | Plan and validate without executing changes |

## Rollback Capability

**FULL** — CRD installation is purely additive: new schema fields are added, old ones remain. Re-applying the previous CRD YAML restores the old schema without touching any stored objects. Controller rollback via `helm rollback` or prior manifest apply is clean. No Certificate, Issuer, or ClusterIssuer object is deleted at any point during upgrade or rollback.

## Smoke Test Requirements

A `kind` cluster running on the Nexplane EC2 instance with cert-manager 1.12 installed via Helm. The smoke test upgrades to 1.13, verifies a self-signed certificate issues successfully post-upgrade, then exercises rollback and confirms the cluster returns to 1.12 with all certificates still present. The kind cluster is provisioned fresh each run (no AMI caching needed — setup is under 60s).

## Key Risks

- **CRD deletion ordering** — Removing old CRDs before applying new ones destroys all Certificate and Issuer resources cluster-wide. The CR mitigates this by only ever using `kubectl apply` (additive), never `kubectl delete`, on CRD objects during upgrade.
- **Webhook race window** — cert-manager's admission webhook is unavailable for ~30s after the controller upgrade; any Certificate object submitted during this window fails. The CR mitigates this with active webhook readiness probing and retry before emitting the success signal.
- **Version skipping** — cert-manager occasionally drops support for old CRD fields in minor releases; skipping versions can leave orphaned objects. The CR validates that the current version is within a supported upgrade band and warns if the hop spans more than one minor version without explicit acknowledgment.
