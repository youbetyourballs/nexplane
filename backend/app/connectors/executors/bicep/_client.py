from azure.identity import ClientSecretCredential
from azure.mgmt.resource import ResourceManagementClient


def get_client(creds: dict) -> ResourceManagementClient:
    credential = ClientSecretCredential(
        tenant_id=creds["tenant_id"],
        client_id=creds["client_id"],
        client_secret=creds["client_secret"],
    )
    return ResourceManagementClient(credential, creds["subscription_id"])
