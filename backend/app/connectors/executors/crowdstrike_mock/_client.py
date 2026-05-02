def get_hosts_api(creds: dict):
    from falconpy import Hosts
    return Hosts(
        client_id=creds["client_id"],
        client_secret=creds["client_secret"],
        base_url=creds.get("base_url", "https://api.crowdstrike.com"),
    )


def get_device_control_api(creds: dict):
    from falconpy import DeviceControlPolicies
    return DeviceControlPolicies(
        client_id=creds["client_id"],
        client_secret=creds["client_secret"],
        base_url=creds.get("base_url", "https://api.crowdstrike.com"),
    )
