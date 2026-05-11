def get_oci_config(creds: dict) -> dict:
    """Build OCI SDK config dict from stored connector credentials."""
    return {
        "user": creds["user"],
        "key_content": creds["private_key"],
        "fingerprint": creds["fingerprint"],
        "tenancy": creds["tenancy"],
        "region": creds["region"],
    }


def get_identity_client(creds: dict):
    """Return an OCI IdentityClient authenticated with stored credentials."""
    import oci
    config = get_oci_config(creds)
    return oci.identity.IdentityClient(config)


def get_compute_client(creds: dict):
    """Return an OCI ComputeClient authenticated with stored credentials."""
    import oci
    config = get_oci_config(creds)
    return oci.core.ComputeClient(config)


def get_network_client(creds: dict):
    """Return an OCI VirtualNetworkClient authenticated with stored credentials."""
    import oci
    config = get_oci_config(creds)
    return oci.core.VirtualNetworkClient(config)


def get_blockstorage_client(creds: dict):
    """Return an OCI BlockstorageClient authenticated with stored credentials."""
    import oci
    config = get_oci_config(creds)
    return oci.core.BlockstorageClient(config)


def get_objectstorage_client(creds: dict):
    """Return an OCI ObjectStorageClient authenticated with stored credentials."""
    import oci
    config = get_oci_config(creds)
    return oci.object_storage.ObjectStorageClient(config)


def get_loadbalancer_client(creds: dict):
    """Return an OCI LoadBalancerClient using stored credentials."""
    import oci
    config = get_oci_config(creds)
    return oci.load_balancer.LoadBalancerClient(config)


def get_dns_client(creds: dict):
    """Return an OCI DnsClient using stored credentials."""
    import oci
    config = get_oci_config(creds)
    return oci.dns.DnsClient(config)


def _make_config(creds: dict) -> dict:
    """Alias for get_oci_config — used internally for vault/KMS client factories."""
    return get_oci_config(creds)


def get_vault_client(creds: dict):
    """Return oci.vault.VaultsClient (for listing secrets and vaults)."""
    import oci
    config = get_oci_config(creds)
    return oci.vault.VaultsClient(config)


def get_secrets_client(creds: dict):
    """Return oci.secrets.SecretsClient (for reading secret bundles)."""
    import oci
    config = get_oci_config(creds)
    return oci.secrets.SecretsClient(config)


def get_vaults_management_client(creds: dict):
    """Return oci.key_management.KmsVaultClient."""
    import oci
    config = get_oci_config(creds)
    return oci.key_management.KmsVaultClient(config)
