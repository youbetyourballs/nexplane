import base64
import yaml
from kubernetes import client, config


def get_k8s_clients(creds: dict):
    kubeconfig_b64 = creds["kubeconfig"]
    kubeconfig_yaml = base64.b64decode(kubeconfig_b64).decode("utf-8")
    kubeconfig_dict = yaml.safe_load(kubeconfig_yaml)
    cfg = client.Configuration()
    config.load_kube_config_from_dict(config_dict=kubeconfig_dict, client_configuration=cfg, context=creds.get("context"))
    api_client = client.ApiClient(cfg)
    return {
        "core": client.CoreV1Api(api_client),
        "apps": client.AppsV1Api(api_client),
        "rbac": client.RbacAuthorizationV1Api(api_client),
        "networking": client.NetworkingV1Api(api_client),
        "api_client": api_client,
    }
