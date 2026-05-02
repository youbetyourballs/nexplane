def get_firewall(creds: dict):
    from panos.firewall import Firewall
    return Firewall(creds["hostname"], api_username=creds["username"], api_password=creds["password"])
