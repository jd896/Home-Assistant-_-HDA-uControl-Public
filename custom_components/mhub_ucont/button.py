from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import logging

_LOGGER = logging.getLogger(__name__)

CONTROL_METHOD_IR = "ir"
CONTROL_METHOD_CEC = "cec"
CONTROL_METHOD_NONE = "none"


def _get_zone_options(entry, zone_id):
    return entry.options.get("zones", {}).get(zone_id, {})


def _command_allowed(zone_opts, command_id):
    commands = zone_opts.get("commands")
    if commands is None:
        return True
    return int(command_id) in [int(c) for c in commands]


def _get_cec_type(coordinator, output_id):
    if output_id == "a":
        return 0
    return 1


async def _fetch_cec_commands(hass, entry):
    try:
        host = entry.data["host"]
        session = async_get_clientsession(hass)
        async with session.get(f"http://{host}/api/data/204/", timeout=10) as resp:
            data = await resp.json(content_type=None)
            return data.get("data", {}).get("cecpack", [])
    except Exception as e:
        _LOGGER.error("Failed to fetch CEC commands: %s", e)
        return []


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up MHUB button entities."""
    coordinator = hass.data["mhub_ucont"][entry.entry_id]
    entry_id = entry.entry_id

    entities = []

    zones = coordinator.data.get("zones", [])
    sources = coordinator.data.get("sources", {})
    ir_devices = coordinator.data.get("ir_devices", {})
    ir_port_to_output = coordinator.data.get("ir_port_to_output", {})
    output_to_zone = coordinator.data.get("output_to_zone", {})
    zones_dict = {zone["zone_id"]: zone["zone_label"] for zone in zones}
    output_to_ir_port = {v: k for k, v in ir_port_to_output.items()}

    # ----------------------------------------
    # INPUT SWITCH BUTTONS — always created
    # ----------------------------------------
    for zone in zones:
        zone_id = zone.get("zone_id")
        zone_label = zone.get("zone_label")
        outputs = zone.get("outputs", [])
        if not outputs:
            continue
        output_id = outputs[0].get("output_id", "").lower()
        if not output_id:
            continue
        for input_id, label in sources.items():
            entities.append(MhubInputButton(
                coordinator, entry_id, zone_id, zone_label, output_id, input_id, label
            ))

    # ----------------------------------------
    # IR BUTTONS
    # Skipped for zones configured as CEC or None
    # Commands filtered by options
    # ----------------------------------------
    for device_key, pack in ir_devices.items():
        port_type = pack.get("_port_type")
        port_id = pack.get("_port_id")
        pack_name = pack.get("name", f"{port_type} {port_id}")

        if port_type == "output":
            output_id = ir_port_to_output.get(port_id)
            zone_id = output_to_zone.get(output_id) if output_id else None
            zone_label = zones_dict.get(zone_id) if zone_id else None
            device_identifier = ("mhub_ucont", f"{entry_id}_display_{device_key}")
            if zone_label and output_id:
                device_name = f"{zone_label} (Output {output_id.upper()}) - {pack_name}"
            elif output_id:
                device_name = f"Output {output_id.upper()} - {pack_name}"
            else:
                device_name = f"Display - {pack_name}"
            model = "MHUB Display IR"
        else:
            zone_id = None
            device_identifier = ("mhub_ucont", f"{entry_id}_source_{device_key}")
            device_name = f"Source - {pack_name}"
            model = "MHUB Source IR"

        zone_opts = _get_zone_options(entry, zone_id) if zone_id else {}
        method = zone_opts.get("control_method")
        if method in (CONTROL_METHOD_CEC, CONTROL_METHOD_NONE):
            _LOGGER.debug("Skipping IR for zone %s — method is %s", zone_id, method)
            continue

        ir_commands = pack.get("ir_pack") or pack.get("irpack", [])
        if not ir_commands:
            continue

        for command in ir_commands:
            command_id = command.get("command_id") or command.get("id")
            command_label = command.get("label")
            if not command_id or not command_label:
                continue
            if not _command_allowed(zone_opts, command_id):
                continue
            entities.append(MhubIRButton(
                coordinator, entry_id, device_identifier, device_name,
                port_id, command_id, command_label, model
            ))

    # ----------------------------------------
    # CEC BUTTONS
    # Only for zones configured as CEC
    # Commands filtered by options
    # ----------------------------------------
    cec_zones = [
        z for z in zones
        if _get_zone_options(entry, z["zone_id"]).get("control_method") == CONTROL_METHOD_CEC
    ]

    if cec_zones:
        cec_commands = await _fetch_cec_commands(hass, entry)
        for zone in cec_zones:
            zone_id = zone["zone_id"]
            zone_label = zone.get("zone_label", zone_id)
            outputs = zone.get("outputs", [])
            if not outputs:
                continue
            output_id = outputs[0].get("output_id", "").lower()
            if not output_id:
                continue
            zone_opts = _get_zone_options(entry, zone_id)
            device_identifier = ("mhub_ucont", f"{entry_id}_cec_{zone_id}")
            device_name = f"{zone_label} - CEC"
            for command in cec_commands:
                command_id = command.get("id")
                command_label = command.get("label")
                if command_id is None or not command_label:
                    continue
                if not _command_allowed(zone_opts, command_id):
                    continue
                entities.append(MhubCECButton(
                    coordinator, entry_id, device_identifier, device_name,
                    output_id, _get_cec_type(coordinator, output_id),
                    command_id, command_label
                ))

    _LOGGER.info(
        "Created %d button entities (%d input, %d IR, %d CEC)",
        len(entities),
        len([e for e in entities if isinstance(e, MhubInputButton)]),
        len([e for e in entities if isinstance(e, MhubIRButton)]),
        len([e for e in entities if isinstance(e, MhubCECButton)]),
    )

    async_add_entities(entities)

    try:
        from .custom_ir import async_setup_custom_ir_buttons
        await async_setup_custom_ir_buttons(hass, entry, async_add_entities, coordinator)
    except Exception as e:
        _LOGGER.error("Failed to load custom IR buttons: %s", e)


class MhubInputButton(CoordinatorEntity, ButtonEntity):
    def __init__(self, coordinator, entry_id, zone_id, zone_label, output_id, input_id, label):
        super().__init__(coordinator)
        self._output_id = output_id
        self._input_id = input_id
        self._attr_name = label
        self._attr_unique_id = f"{entry_id}_{zone_id}_input_{input_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={("mhub_ucont", f"{entry_id}_{zone_id}")},
            name=zone_label,
            manufacturer="HDANYWHERE",
            model=coordinator.data.get("device_info", {}).get("model", "MHUB Zone"),
            serial_number=coordinator.data.get("device_info", {}).get("serial_number"),
            sw_version=coordinator.data.get("device_info", {}).get("firmware"),
            hw_version=coordinator.data.get("device_info", {}).get("unit_id"),
            configuration_url=f"http://{coordinator.data.get('device_info', {}).get('ip_address', coordinator.api._host)}",
        )

    async def async_press(self):
        try:
            await self.coordinator.api.switch_output_input(self._output_id, self._input_id)
            self.coordinator.async_request_refresh()
        except Exception as e:
            _LOGGER.error("Failed to switch output %s to input %s: %s", self._output_id, self._input_id, e)
            raise


class MhubIRButton(CoordinatorEntity, ButtonEntity):
    def __init__(self, coordinator, entry_id, device_identifier, device_name, port_id, command_id, command_label, model):
        super().__init__(coordinator)
        self._port_id = port_id
        self._command_id = command_id
        self._attr_name = command_label
        self._attr_unique_id = f"{entry_id}_ir_{device_identifier[1]}_{command_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={device_identifier},
            name=device_name,
            manufacturer="HDANYWHERE",
            model=model,
        )

    async def async_press(self):
        try:
            await self.coordinator.api.send_ir(self._port_id, self._command_id)
        except Exception as e:
            _LOGGER.error("Failed to send IR command %s to port %s: %s", self._command_id, self._port_id, e)
            raise


class MhubCECButton(CoordinatorEntity, ButtonEntity):
    def __init__(self, coordinator, entry_id, device_identifier, device_name, output_id, cec_type, command_id, command_label):
        super().__init__(coordinator)
        self._output_id = output_id
        self._cec_type = cec_type
        self._command_id = command_id
        self._attr_name = command_label
        self._attr_unique_id = f"{entry_id}_cec_{device_identifier[1]}_{command_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={device_identifier},
            name=device_name,
            manufacturer="HDANYWHERE",
            model="MHUB CEC",
        )

    async def async_press(self):
        try:
            await self.coordinator.api.send_cec(
                self._output_id, self._cec_type, self._command_id
            )
        except Exception as e:
            _LOGGER.error(
                "Failed to send CEC command %s to output %s: %s",
                self._command_id, self._output_id, e
            )
            raise