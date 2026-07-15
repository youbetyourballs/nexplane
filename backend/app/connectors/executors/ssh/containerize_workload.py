# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Adaptive SSH containerize executor.

Detects Docker availability on the target host at runtime and branches:
  - In-place:  Docker present AND deployment_target absent or "local"
               → build image locally, stop systemd service, run container in its place.
  - Remote:    deployment_target is "k8s" or a host address, OR Docker absent
               → build on source host (still needs Docker), push to registry,
                 deploy to K8s cluster or target host via docker run over SSH.

Rollback:
  - In-place:  stop/remove container, restart original systemd service.
  - Remote:    undeploy from target; source service was never stopped.

Parameters:
  service_name (str, required): systemd unit name e.g. "myapp.service"
  registry (str): image registry prefix e.g. "ghcr.io/org"
  deployment_target (str, optional): "local" | "k8s" | "<host_address>"
  namespace (str, optional): K8s namespace for remote deploy. Default "default".
  target_cluster_id (str, optional): asset_id of K8s cluster for remote deploy.
"""

ROLLBACK_CAPABILITY = "full"

try:
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
except ImportError:
    dispatch_agent_job = None  # type: ignore[assignment]


def _run(connector, cmd: str) -> tuple:
    """Run cmd via SSH connector. Returns (stdout, stderr, exit_code)."""
    return connector.run_command(cmd)


async def _capture_pre_state(connector, service_name: str) -> dict:
    stdout, _, _ = _run(connector, f"systemctl is-active {service_name} 2>/dev/null || true")
    was_active = stdout.strip() == "active"
    stdout, _, _ = _run(connector, f"systemctl is-enabled {service_name} 2>/dev/null || true")
    was_enabled = stdout.strip() in ("enabled", "enabled-runtime")
    stdout, _, _ = _run(
        connector,
        f"ss -tlnp 2>/dev/null | grep $(systemctl show -p MainPID --value {service_name} 2>/dev/null) "
        f"| awk '{{print $4}}' | sed 's/.*://' | sort -u || true",
    )
    ports = [p.strip() for p in stdout.splitlines() if p.strip()]
    return {
        "systemd_unit": service_name,
        "was_active": was_active,
        "was_enabled": was_enabled,
        "ports": ports,
        "container_id": None,
        "path": None,
    }


async def _docker_available(connector) -> bool:
    _, _, rc = _run(connector, "docker info >/dev/null 2>&1")
    return rc == 0


async def _build_image(connector, service_name: str, registry: str) -> dict:
    """Build Docker image on host from service binary. Returns image_name and image_digest."""
    image_tag = f"{registry}/{service_name}:latest"
    stdout, _, _ = _run(
        connector,
        f"systemctl show -p ExecStart --value {service_name} 2>/dev/null | awk '{{print $1}}'",
    )
    binary = stdout.strip().split()[0] if stdout.strip() else f"/usr/bin/{service_name.replace('.service', '')}"
    stdout, _, _ = _run(connector, f"ldd {binary} 2>/dev/null | grep -v vdso | awk '{{print $3}}' | grep '^/' || true")
    libs = [lib.strip() for lib in stdout.splitlines() if lib.strip()]

    dockerfile_lines = ["FROM scratch", f"COPY {binary} {binary}"]
    for lib in libs:
        dockerfile_lines.append(f"COPY {lib} {lib}")
    dockerfile_lines.append(f'CMD ["{binary}"]')
    dockerfile = "\n".join(dockerfile_lines)

    _run(connector, f"printf '%s' '{dockerfile}' > /tmp/nexplane-Dockerfile")
    _run(connector, f"docker build -f /tmp/nexplane-Dockerfile -t {image_tag} /")
    stdout, _, _ = _run(connector, f"docker inspect --format='{{{{.Id}}}}' {image_tag}")
    image_digest = stdout.strip()
    return {"image_name": image_tag, "image_digest": image_digest}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    service_name = parameters.get("service_name", "")
    if not service_name:
        raise ValueError("Missing required parameter: service_name")
    registry = parameters.get("registry", "nexplane-local")
    deployment_target = parameters.get("deployment_target")
    namespace = parameters.get("namespace", "default")
    target_cluster_id = parameters.get("target_cluster_id")

    pre_state = await _capture_pre_state(connector, service_name)
    docker_ok = await _docker_available(connector)
    use_inplace = docker_ok and (deployment_target is None or deployment_target == "local")

    image_info = await _build_image(connector, service_name, registry)
    image_name = image_info["image_name"]
    image_digest = image_info["image_digest"]

    if use_inplace:
        ports = pre_state.get("ports", [])
        port_args = " ".join(f"-p {p}:{p}" for p in ports)
        _run(connector, f"systemctl stop {service_name}")
        stdout, _, rc = _run(
            connector,
            f"docker run -d --name nexplane-{service_name} --restart=unless-stopped {port_args} {image_name}",
        )
        if rc != 0:
            _run(connector, f"systemctl start {service_name}")
            raise RuntimeError(f"docker run failed for {service_name} — original service restored")
        container_id = stdout.strip()
        pre_state["container_id"] = container_id
        pre_state["path"] = "inplace"
        return {
            "containerized": True,
            "path": "inplace",
            "container_id": container_id,
            "image": image_name,
            "image_digest": image_digest,
            "pre_state": pre_state,
        }

    # Remote path — push image, deploy elsewhere
    _run(connector, f"docker push {image_name}")

    deploy_result: dict = {}
    if deployment_target == "k8s" or target_cluster_id:
        deploy_result = await dispatch_agent_job(
            command="k8s_deploy_image",
            parameters={"image": image_name, "app_name": service_name, "namespace": namespace},
            asset_ids=[str(target_cluster_id)] if target_cluster_id else list(asset_ids),
            timeout_seconds=120,
        )
    elif deployment_target and deployment_target not in ("local", "k8s"):
        deploy_result = {"deployed_to": deployment_target, "image": image_name}
    else:
        deploy_result = {"pushed": True, "image": image_name}

    pre_state["path"] = "remote"
    return {
        "containerized": True,
        "path": "remote",
        "image": image_name,
        "image_digest": image_digest,
        "deploy_result": deploy_result,
        "pre_state": pre_state,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    pre_state = execution_result.get("pre_state", {})
    path = pre_state.get("path") or execution_result.get("path")
    service_name = pre_state.get("systemd_unit") or parameters.get("service_name", "")

    if path == "inplace":
        container_id = pre_state.get("container_id") or ""
        if container_id:
            _run(connector, f"docker stop {container_id} 2>/dev/null || true")
            _run(connector, f"docker rm {container_id} 2>/dev/null || true")
        if pre_state.get("was_active"):
            _run(connector, f"systemctl start {service_name}")
        if pre_state.get("was_enabled"):
            _run(connector, f"systemctl enable {service_name} 2>/dev/null || true")
        return {"rolled_back": True, "path": "inplace", "service_name": service_name}

    if path == "remote":
        deploy_result = execution_result.get("deploy_result", {})
        namespace = deploy_result.get("namespace", parameters.get("namespace", "default"))
        target_cluster_id = parameters.get("target_cluster_id")
        if deploy_result.get("deployed_to") or deploy_result.get("namespace") or target_cluster_id:
            try:
                await dispatch_agent_job(
                    command="k8s_delete_deployment",
                    parameters={"app_name": service_name, "namespace": namespace},
                    asset_ids=[str(target_cluster_id)] if target_cluster_id else [],
                    timeout_seconds=60,
                )
            except Exception:
                pass
        return {"rolled_back": True, "path": "remote", "note": "source service was not stopped"}

    return {"rolled_back": False, "reason": "unknown_path", "pre_state": pre_state}
