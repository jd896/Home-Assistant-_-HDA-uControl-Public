import voluptuous as vol
import logging
import re

from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv

_LOGGER = logging.getLogger(__name__)

CONTROL_METHOD_IR = "ir"
CONTROL_METHOD_CEC = "cec"
CONTROL_METHOD_NONE = "none"


class MhubConfigFlow(config_entries.ConfigFlow, domain="mhub_ucont"):
    """Handle a config flow for MHUB uControl."""

    VERSION = 1

    def __init__(self):
        self._title = ""
        self._data = {}

    @staticmethod
    def async_get_options_flow(config_entry):
        return MhubOptionsFlow()

    async def async_step_user(self, user_input=None):
        errors = {}

        if user_input is not None:
            host = user_input[CONF_HOST]
            validation_result = await self._validate_host(host)

            if validation_result["valid"]:
                unique_id = validation_result.get("serial_number", host)
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                self._title = validation_result.get("mhub_name") or host
                self._data = user_input
                return await self.async_step_confirm()
            else:
                errors["base"] = validation_result.get("error", "cannot_connect")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_HOST): str}),
            errors=errors,
        )

    async def async_step_confirm(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title=self._title, data=self._data)

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders={"mhub_name": self._title},
        )

    async def _validate_host(self, host):
        if not host:
            return {"valid": False, "error": "invalid_host"}

        host = host.replace("http://", "").replace("https://", "").rstrip("/")

        ip_pattern = r'^(\d{1,3}\.){3}\d{1,3}$'
        hostname_pattern = r'^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$'

        if not (re.match(ip_pattern, host) or re.match(hostname_pattern, host)):
            return {"valid": False, "error": "invalid_host"}

        try:
            session = async_get_clientsession(self.hass)
            async with session.get(
                f"http://{host}/api/data/100/", timeout=10
            ) as response:
                if response.status != 200:
                    return {"valid": False, "error": "cannot_connect"}

                data = await response.json(content_type=None)
                inner = data.get("data", {})
                mhub_data = inner.get("os") or inner.get("mhub", {})
                mhub_name = mhub_data.get("mhub_name")
                serial_number = mhub_data.get("serial_number")

                _LOGGER.info(
                    "Successfully connected to MHUB: %s (Serial: %s)",
                    mhub_name, serial_number
                )
                return {"valid": True, "mhub_name": mhub_name, "serial_number": serial_number}

        except TimeoutError:
            return {"valid": False, "error": "timeout_connect"}
        except Exception as e:
            _LOGGER.error("Error connecting to MHUB at %s: %s", host, str(e))
            return {"valid": False, "error": "cannot_connect"}


class MhubOptionsFlow(config_entries.OptionsFlow):
    """
    Options flow for per-zone control method and command selection.

    Stored in entry.options as:
    {
        "zones": {
            "z1": {
                "control_method": "ir" | "cec" | "none",
                "commands": [1, 2, 25, ...]  # selected command IDs, None = all
            },
            ...
        }
    }

    If entry.options is empty, all available commands are presented by default.
    """

    def __init__(self):
        self._zone_methods = {}
        self._zone_commands = {}
        self._zones = []
        self._zones_needing_commands = []
        self._current_zone = None
        self._ir_commands = {}
        self._cec_commands = []
        self._zone_key_map = {}

    async def async_step_init(self, user_input=None):
        """Step 1 — choose control method per zone."""
        coordinator = self.hass.data["mhub_ucont"].get(self.config_entry.entry_id)
        if not coordinator:
            return self.async_abort(reason="not_loaded")

        self._zones = coordinator.data.get("zones", [])
        existing_zones = self.config_entry.options.get("zones", {})

        if user_input is not None:
            if not self._zone_key_map:
                for zone in self._zones:
                    zone_id = zone["zone_id"]
                    zone_label = zone.get("zone_label", zone_id)
                    field_key = f"zone_{zone_label.lower().replace(' ', '_').replace('/', '_')}"
                    self._zone_key_map[field_key] = zone_id

            for field_key, zone_id in self._zone_key_map.items():
                self._zone_methods[zone_id] = user_input.get(
                    field_key, CONTROL_METHOD_NONE
                )

            if any(m == CONTROL_METHOD_CEC for m in self._zone_methods.values()):
                self._cec_commands = await self._fetch_cec_commands()

            ir_port_to_output = coordinator.data.get("ir_port_to_output", {})
            output_to_zone = coordinator.data.get("output_to_zone", {})
            zone_to_output = {v: k for k, v in output_to_zone.items()}
            output_to_ir_port = {v: k for k, v in ir_port_to_output.items()}

            for zone in self._zones:
                zone_id = zone["zone_id"]
                if self._zone_methods.get(zone_id) == CONTROL_METHOD_IR:
                    output_id = zone_to_output.get(zone_id)
                    ir_port = output_to_ir_port.get(output_id) if output_id else None
                    if ir_port:
                        self._ir_commands[zone_id] = await self._fetch_ir_commands(
                            ir_port, coordinator
                        )
                    else:
                        self._ir_commands[zone_id] = []
                        _LOGGER.warning(
                            "Zone %s has IR selected but no IR port mapping found",
                            zone_id
                        )

            self._zones_needing_commands = [
                zone["zone_id"] for zone in self._zones
                if self._zone_methods.get(zone["zone_id"]) in (
                    CONTROL_METHOD_IR, CONTROL_METHOD_CEC
                )
            ]

            return await self._next_zone_step()

        # Build schema — one dropdown per zone, labelled with zone name
        schema_dict = {}
        self._zone_key_map = {}

        for zone in self._zones:
            zone_id = zone["zone_id"]
            zone_label = zone.get("zone_label", zone_id)
            current_method = existing_zones.get(zone_id, {}).get(
                "control_method", CONTROL_METHOD_NONE
            )
            field_key = f"zone_{zone_label.lower().replace(' ', '_').replace('/', '_')}"
            self._zone_key_map[field_key] = zone_id
            schema_dict[
                vol.Optional(field_key, default=current_method)
            ] = vol.In({
                CONTROL_METHOD_IR: "IR (uControl pack)",
                CONTROL_METHOD_CEC: "CEC",
                CONTROL_METHOD_NONE: "None",
            })

        zone_list = "\n".join(
            f"• {zone.get('zone_label', zone['zone_id'])}" for zone in self._zones
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema_dict),
            description_placeholders={
                "zone_count": str(len(self._zones)),
                "zone_list": zone_list,
            },
        )

    async def async_step_zone_commands(self, user_input=None):
        """Step 2..N — command selection for current zone."""
        if user_input is not None:
            selected = user_input.get("commands", [])
            self._zone_commands[self._current_zone] = [int(c) for c in selected]
            return await self._next_zone_step()

        zone_id = self._current_zone
        method = self._zone_methods.get(zone_id)
        zone_label = next(
            (z.get("zone_label", zone_id) for z in self._zones if z["zone_id"] == zone_id),
            zone_id
        )
        existing_zones = self.config_entry.options.get("zones", {})
        existing_commands = existing_zones.get(zone_id, {}).get("commands")

        commands = (
            self._ir_commands.get(zone_id, [])
            if method == CONTROL_METHOD_IR
            else self._cec_commands
        )

        if not commands:
            _LOGGER.warning(
                "No commands available for zone %s (%s) — skipping",
                zone_id, method
            )
            self._zone_commands[zone_id] = None
            return await self._next_zone_step()

        command_options = {
            str(cmd["id"]): cmd.get("label", str(cmd["id"]))
            for cmd in commands
            if cmd.get("id") is not None
        }

        if existing_commands is None:
            default_commands = list(command_options.keys())
        else:
            default_commands = [
                str(c) for c in existing_commands if str(c) in command_options
            ]

        return self.async_show_form(
            step_id="zone_commands",
            data_schema=vol.Schema({
                vol.Optional("commands", default=default_commands): cv.multi_select(
                    command_options
                )
            }),
            description_placeholders={
                "zone_label": zone_label,
                "method": method.upper(),
                "command_count": str(len(commands)),
            },
        )

    async def _next_zone_step(self):
        if self._zones_needing_commands:
            self._current_zone = self._zones_needing_commands.pop(0)
            return await self.async_step_zone_commands()
        return self._save_options()

    def _save_options(self):
        zones_options = {}
        for zone in self._zones:
            zone_id = zone["zone_id"]
            method = self._zone_methods.get(zone_id, CONTROL_METHOD_NONE)
            if method == CONTROL_METHOD_NONE:
                zones_options[zone_id] = {"control_method": CONTROL_METHOD_NONE}
            else:
                zones_options[zone_id] = {
                    "control_method": method,
                    "commands": self._zone_commands.get(zone_id),
                }

        _LOGGER.info("Saving MHUB zone options for %s", self.config_entry.title)
        return self.async_create_entry(title="", data={"zones": zones_options})

    async def _fetch_ir_commands(self, port_id, coordinator):
        try:
            stacked = coordinator.data.get("stacked", False)
            details = await coordinator.api.get_ir_pack_details(port_id, stacked)
            if not details:
                return []
            if details.get("header", {}).get("error"):
                return []
            pack = details.get("data", {})
            return pack.get("ir_pack") or pack.get("irpack") or []
        except Exception as e:
            _LOGGER.error("Failed to fetch IR commands for port %s: %s", port_id, e)
            return []

    async def _fetch_cec_commands(self):
        try:
            host = self.config_entry.data["host"]
            session = async_get_clientsession(self.hass)
            async with session.get(
                f"http://{host}/api/data/204/", timeout=10
            ) as resp:
                data = await resp.json(content_type=None)
                return data.get("data", {}).get("cecpack", [])
        except Exception as e:
            _LOGGER.error("Failed to fetch CEC commands: %s", e)
            return []