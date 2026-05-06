from azure.identity import ClientSecretCredential


def get_credential(creds: dict):
    return ClientSecretCredential(creds['tenant_id'], creds['client_id'], creds['client_secret'])


def get_network_client(creds: dict):
    from azure.mgmt.network import NetworkManagementClient
    return NetworkManagementClient(get_credential(creds), creds['subscription_id'])


def get_compute_client(creds: dict):
    from azure.mgmt.compute import ComputeManagementClient
    return ComputeManagementClient(get_credential(creds), creds['subscription_id'])


def get_storage_client(creds: dict):
    from azure.mgmt.storage import StorageManagementClient
    return StorageManagementClient(get_credential(creds), creds['subscription_id'])


def get_msi_client(creds: dict):
    from azure.mgmt.msi import ManagedServiceIdentityClient
    return ManagedServiceIdentityClient(get_credential(creds), creds['subscription_id'])


def get_authorization_client(creds: dict):
    from azure.mgmt.authorization import AuthorizationManagementClient
    return AuthorizationManagementClient(get_credential(creds), creds['subscription_id'])


def get_dns_client(creds: dict):
    from azure.mgmt.dns import DnsManagementClient
    return DnsManagementClient(get_credential(creds), creds['subscription_id'])
