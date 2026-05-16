from __future__ import annotations

import urllib.parse

import httpx


def _bool(val) -> bool:
    if isinstance(val, bool):
        return val
    return str(val).lower() in ("true", "1", "yes")


class SCCMClient:
    """Thin client for the SCCM / MECM AdminService OData REST API.

    AdminService exposes two roots:
      - https://{server}/AdminService/wmi/   — WMI class queries
      - https://{server}/AdminService/v1.0/  — newer REST endpoints

    Authentication: NTLM (preferred) or HTTP Basic.
    """

    def __init__(
        self,
        server: str,
        username: str,
        password: str,
        site_code: str,
        verify_ssl: bool = False,
        use_ntlm: bool = True,
    ) -> None:
        self._server = server.rstrip("/")
        self._username = username
        self._password = password
        self._site_code = site_code
        self._verify_ssl = verify_ssl
        self._use_ntlm = use_ntlm

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _wmi_url(self, path: str) -> str:
        return f"https://{self._server}/AdminService/wmi/{path.lstrip('/')}"

    def _v1_url(self, path: str) -> str:
        return f"https://{self._server}/AdminService/v1.0/{path.lstrip('/')}"

    def _auth(self):
        """Return an httpx-compatible auth object or tuple."""
        if self._use_ntlm:
            try:
                from httpx_ntlm import HttpNtlmAuth  # type: ignore
                return HttpNtlmAuth(self._username, self._password)
            except ImportError:
                pass
            try:
                from requests_ntlm import HttpNtlmAuth as _RNtlm  # type: ignore
                # requests_ntlm is not directly compatible with httpx but we fall
                # through to basic auth rather than crash.
            except ImportError:
                pass
        return (self._username, self._password)

    def _client(self) -> httpx.Client:
        return httpx.Client(
            auth=self._auth(),
            verify=self._verify_ssl,
            timeout=60.0,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )

    def _get(self, url: str) -> dict:
        with self._client() as c:
            r = c.get(url)
            r.raise_for_status()
            return r.json()

    def _post(self, url: str, payload: dict) -> dict:
        with self._client() as c:
            r = c.post(url, json=payload)
            r.raise_for_status()
            return r.json()

    def _delete(self, url: str) -> dict:
        with self._client() as c:
            r = c.delete(url)
            r.raise_for_status()
            try:
                return r.json()
            except Exception:
                return {"status_code": r.status_code}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_collections(self) -> list[dict]:
        """Return all device/user collections visible to the service account."""
        url = self._wmi_url("SMS_Collection")
        data = self._get(url)
        return data.get("value", [])

    def get_collection_members(self, collection_id: str) -> list[dict]:
        """Return members of a specific collection."""
        # SMS_FullCollectionMembership keyed by CollectionID
        encoded = urllib.parse.quote(f"CollectionID='{collection_id}'")
        url = self._wmi_url(f"SMS_FullCollectionMembership?$filter=CollectionID eq '{collection_id}'")
        data = self._get(url)
        return data.get("value", [])

    def deploy_application(
        self,
        app_name: str,
        collection_id: str,
        deployment_purpose: str = "Required",
    ) -> dict:
        """Deploy an application to a collection.

        deployment_purpose: 'Required' or 'Available'
        Returns the created deployment object (includes DeploymentID).
        """
        # Map purpose string to SCCM int (2 = Required, 1 = Available)
        purpose_map = {"required": 2, "available": 1}
        purpose_int = purpose_map.get(deployment_purpose.lower(), 2)

        payload = {
            "ApplicationName": app_name,
            "CollectionID": collection_id,
            "OfferTypeID": purpose_int,
            "UpdateSupersedence": False,
        }
        url = self._wmi_url("SMS_ApplicationAssignment")
        return self._post(url, payload)

    def remove_deployment(self, deployment_id: str) -> dict:
        """Remove an application deployment by its DeploymentID."""
        url = self._wmi_url(f"SMS_ApplicationAssignment/{deployment_id}")
        return self._delete(url)

    def run_script(self, script_guid: str, collection_id: str) -> dict:
        """Run a pre-approved SCCM script against a collection.

        Scripts must be approved in the SCCM console before invocation.
        """
        payload = {
            "ScriptGuid": script_guid,
            "CollectionID": collection_id,
        }
        url = self._v1_url("Scripts/RunScript")
        return self._post(url, payload)

    def get_device(self, device_name: str) -> dict:
        """Return the SMS_R_System record for a device by name."""
        url = self._wmi_url(f"SMS_R_System?$filter=Name eq '{device_name}'")
        data = self._get(url)
        items = data.get("value", [])
        if not items:
            raise ValueError(f"Device '{device_name}' not found in SCCM")
        return items[0]

    def get_software_inventory(self, device_name: str) -> list[dict]:
        """Return installed software for a device (SMS_G_System_ADD_REMOVE_PROGRAMS)."""
        device = self.get_device(device_name)
        resource_id = device.get("ResourceID") or device.get("resourceId", "")
        url = self._wmi_url(
            f"SMS_G_System_ADD_REMOVE_PROGRAMS?$filter=ResourceID eq {resource_id}"
        )
        data = self._get(url)
        return data.get("value", [])

    def trigger_policy_evaluation(self, device_name: str) -> dict:
        """Trigger a Machine Policy Retrieval & Evaluation Cycle on a device."""
        # This uses the CMPivot/Script endpoint or the WMI TriggerSchedule method.
        # We send a RunScript call with the well-known Machine Policy schedule ID.
        # Schedule ID for Machine Policy Retrieval: {00000000-0000-0000-0000-000000000021}
        # We POST to SMS_Client method via AdminService v1.0 Device resource.
        device = self.get_device(device_name)
        resource_id = device.get("ResourceID") or device.get("resourceId", "")
        url = self._v1_url(f"Device({resource_id})/AdminService.TriggerClientOperation")
        payload = {"ClientOperation": 8}  # 8 = MachinePolicyRetrieveEvalCycle
        return self._post(url, payload)

    def trigger_collection_action(self, collection_id: str, action: str) -> dict:
        """Trigger a named client action on all members of a collection.

        action: one of MachinePolicyRetrieve, SoftwareInventory,
                HardwareInventory, UpdateDeploymentReEval
        """
        action_map = {
            "machinepolicyretrieve": 8,
            "softwareinventory": 1,
            "hardwareinventory": 1,
            "updatedeploymentreeval": 113,
        }
        op_id = action_map.get(action.lower(), 8)
        url = self._v1_url(f"Collection('{collection_id}')/AdminService.TriggerClientOperation")
        payload = {"ClientOperation": op_id}
        return self._post(url, payload)


def get_sccm_client(connector) -> SCCMClient:
    creds = getattr(connector, "credentials", {}) or {}
    server = creds.get("server", "")
    username = creds.get("username", "")
    password = creds.get("password", "")
    site_code = creds.get("site_code", "")
    verify_ssl = _bool(creds.get("verify_ssl", "false"))
    use_ntlm = _bool(creds.get("use_ntlm", "true"))
    return SCCMClient(
        server=server,
        username=username,
        password=password,
        site_code=site_code,
        verify_ssl=verify_ssl,
        use_ntlm=use_ntlm,
    )
