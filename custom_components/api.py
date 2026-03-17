import logging

_LOGGER = logging.getLogger(__name__)


class MhubAPI:
    def __init__(self, host, session):
        self._host = host
        self._session = session
        self._api_version = None  # Will be populated from /api/data/100/

    # -------------------------------------------------
    # INTERNAL GET WRAPPER
    # -------------------------------------------------

    async def _get(self, path):
        url = f"http://{self._host}{path}"

        async with self._session.get(url) as resp:
            try:
                return await resp.json(content_type=None)
            except Exception as e:
                text = await resp.text()
                _LOGGER.debug("MHUB non-JSON response: %s", text)
                _LOGGER.debug("Exception: %s", e)
                return text

    async def _post(self, path, payload):
        """POST request with JSON payload."""
        url = f"http://{self._host}{path}"
        
        async with self._session.post(url, json=payload) as resp:
            try:
                return await resp.json(content_type=None)
            except Exception as e:
                _LOGGER.error("MHUB POST error: %s", e)
                return None

    # -------------------------------------------------
    # SYSTEM DATA
    # -------------------------------------------------

    async def get_system_info(self):
        """Get system information (API endpoint 100).
        
        Also extracts and caches the API version for reference.
        """
        response = await self._get("/api/data/100/")
        
        # Extract and cache API version
        if response and isinstance(response, dict):
            mhub_data = response.get("data", {}).get("mhub", {})
            self._api_version = mhub_data.get("api")
            if self._api_version:
                _LOGGER.info("Detected MHUB API version: %s", self._api_version)
        
        return response

    @property
    def api_version(self):
        """Return the detected API version (2.0, 2.1, etc.)."""
        return self._api_version

    async def get_zones(self):
        """Get zone configuration (API endpoint 102)."""
        return await self._get("/api/data/102/")

    async def get_state(self, stacked=False):
        """Get system state.
        
        Args:
            stacked: True for stacked systems (endpoint 203), False for standalone (endpoint 200)
        """
        if stacked:
            return await self._get("/api/data/203/")
        return await self._get("/api/data/200/")

    # -------------------------------------------------
    # VIDEO SWITCHING
    # (Page 40 of API spec)
    # -------------------------------------------------

    async def switch_output_input(self, output_id, input_id):
        """Switch input source for an output.
        
        Args:
            output_id: Output identifier (a, b, c...)
            input_id: Input identifier (1, 2, 3...)
        """
        return await self._get(
            f"/api/control/switch/{output_id}/{input_id}/"
        )

    # -------------------------------------------------
    # VOLUME (Output based)
    # (Page 43 of API spec)
    # -------------------------------------------------

    async def set_output_volume(self, output_id, volume):
        """Set volume for a specific output.
        
        IMPORTANT: This method uses output_id (a, b, c...), not zone_id.
        The API requires output identifiers per the spec page 43:
        /api/control/volume/[ox]/[vy]/
        
        Args:
            output_id: Output identifier (a, b, c...)
            volume: Volume level (0-100)
        """
        return await self._get(
            f"/api/control/volume/{output_id}/{volume}/"
        )

    async def set_zone_volume_audio(self, zone_id, volume):
        """Set zone volume for MHUB AUDIO systems only.
        
        This is a separate endpoint specifically for MHUB AUDIO systems
        that support multi-output zones (API spec page 45).
        
        Args:
            zone_id: Zone identifier (z1, z2, z3...)
            volume: Volume level (0-100)
        """
        return await self._get(
            f"/api/control/volume/zone/{zone_id}/{volume}/"
        )

    # -------------------------------------------------
    # MUTE (Output based)
    # (Page 47 of API spec)
    # -------------------------------------------------

    async def set_output_mute(self, output_id, mute):
        """Set mute for a specific output.
        
        IMPORTANT: This method uses output_id (a, b, c...), not zone_id.
        The API requires output identifiers per the spec page 47:
        /api/control/mute/[ox]/[mx]/
        
        Args:
            output_id: Output identifier (a, b, c...)
            mute: Boolean mute state
        """
        mute_value = "true" if mute else "false"
        return await self._get(
            f"/api/control/mute/{output_id}/{mute_value}/"
        )

    async def set_zone_mute_audio(self, zone_id, mute):
        """Set zone mute for MHUB AUDIO systems only.
        
        This is a separate endpoint specifically for MHUB AUDIO systems
        that support multi-output zones (API spec page 48).
        
        Args:
            zone_id: Zone identifier (z1, z2, z3...)
            mute: Boolean mute state
        """
        mute_value = "true" if mute else "false"
        return await self._get(
            f"/api/control/mute/zone/{zone_id}/{mute_value}/"
        )

    # -------------------------------------------------
    # IR PACK SUMMARY
    # (Page 31 & 37)
    # -------------------------------------------------

    async def get_ir_packs(self, stacked=False):
        """Get IR pack summary.
        
        Args:
            stacked: True for stacked systems (endpoint 205), False for standalone (endpoint 201)
        """
        if stacked:
            return await self._get("/api/data/205/")
        return await self._get("/api/data/201/")

    # -------------------------------------------------
    # IR PACK DETAILS
    # (Page 32 & 38)
    # -------------------------------------------------

    async def get_ir_pack_details(self, port_id, stacked=False):
        """Get detailed IR pack information for a specific port.
        
        Args:
            port_id: IR port identifier
            stacked: True for stacked systems (endpoint 205), False for standalone (endpoint 201)
        """
        if stacked:
            return await self._get(f"/api/data/205/{port_id}/")
        return await self._get(f"/api/data/201/{port_id}/")

    # -------------------------------------------------
    # SEND IR COMMAND
    # (Page 58)
    # -------------------------------------------------

    async def send_ir(self, port_id, command_id):
        """Send an IR command.
        
        Args:
            port_id: IR port identifier
            command_id: Command identifier from the IR pack
        """
        return await self._get(
            f"/api/command/ir/{port_id}/{command_id}/"
        )

    # -------------------------------------------------
    # SEND PRONTO IR PASSTHROUGH
    # (Page 61)
    # -------------------------------------------------

    async def send_pronto_ir(self, port_id, pronto_code):
        """Send raw Pronto IR hex code."""
        payload = {"irdata": pronto_code}
        return await self._post(f"/api/command/irpass/{port_id}/", payload)

    # -------------------------------------------------
    # SEND CEC COMMAND
    # (Page 62)
    # -------------------------------------------------

    async def send_cec(self, output_id, cec_type, command_id):
        """Send a CEC command via uControl CEC pack.
        
        Args:
            output_id: Output identifier (a, b, c...)
            cec_type: 0=HDMI output, 1=HDBaseT output
            command_id: CEC command ID from data/204
        """
        return await self._post(
            f"/api/command/cec/{output_id}/{cec_type}/{command_id}/",
            {}
        )