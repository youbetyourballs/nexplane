import hvac


def get_vault_client(creds: dict) -> hvac.Client:
    kwargs = {"url": creds["vault_addr"]}
    if creds.get("namespace"):
        kwargs["namespace"] = creds["namespace"]

    client = hvac.Client(**kwargs)

    if creds.get("token"):
        client.token = creds["token"]
    elif creds.get("role_id") and creds.get("secret_id"):
        result = client.auth.approle.login(role_id=creds["role_id"], secret_id=creds["secret_id"])
        client.token = result["auth"]["client_token"]
    else:
        raise ValueError("Either token or role_id+secret_id must be provided")

    return client
