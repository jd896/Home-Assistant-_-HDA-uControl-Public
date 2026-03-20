"""Custom IR button entities for MHUB uControl."""
import logging
from pathlib import Path

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

_LOGGER = logging.getLogger(__name__)

DOMAIN = "mhub_ucont"


async def async_setup_custom_ir_buttons(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    coordinator,
) -> None:
    """Set up custom IR button entities from YAML configuration."""

    integration_path = Path(__file__).parent
    config_file = integration_path / "custom_ir_devices.yaml"

    if not config_file.exists():
        _LOGGER.info("No custom_ir_devices.yaml found, skipping custom IR devices")
        return

    try:
        from homeassistant.util import yaml
        config = await hass.async_add_executor_job(
            yaml.load_yaml, str(config_file)
        )
    except Exception as e:
        _LOGGER.error("Failed to load custom_ir_devices.yaml: %s", e)
        return

    if not config or "custom_ir_devices" not in config:
        _LOGGER.info("No custom_ir_devices defined in configuration")
        return

    devices = config["custom_ir_devices"]
    if not devices:
        _LOGGER.info("custom_ir_devices list is empty")
        return

    entry_title = config_entry.title
    entities = []

    for device in devices:
        device_name = device.get("name")
        target = device.get("target")
        buttons = device.get("buttons", [])
        mhub_filter = device.get("mhub")

        if mhub_filter and mhub_filter != entry_title:
            _LOGGER.debug(
                "Skipping custom IR device '%s' - assigned to '%s', this entry is '%s'",
                device_name, mhub_filter, entry_title
            )
            continue

        if not device_name or not target or not buttons:
            _LOGGER.warning(
                "Invalid custom IR device config: name=%s, target=%s, buttons=%s",
                device_name, target, len(buttons) if buttons else 0
            )
            continue

        device_key = device_name.lower().replace(" ", "_").replace("/", "_")
        device_identifier = (DOMAIN, f"{config_entry.entry_id}_custom_ir_{device_key}")

        _LOGGER.info(
            "Loading custom IR device '%s' with %d buttons (target=%s, mhub=%s)",
            device_name, len(buttons), target, entry_title
        )

        # Resolve via_device from target — link to zone device if target is an output
        via_device = None
        if target.startswith("output_"):
            output_letter = target.replace("output_", "").lower()
            output_to_zone = coordinator.data.get("output_to_zone", {})
            zone_id = output_to_zone.get(output_letter)
            if zone_id:
                via_device = ("mhub_ucont", f"{config_entry.entry_id}_{zone_id}")
                _LOGGER.debug(
                    "Custom IR device '%s' linked to zone %s via output %s",
                    device_name, zone_id, output_letter
                )

        for button_config in buttons:
            button_name = button_config.get("name")
            pronto_code = button_config.get("pronto_code")

            if not button_name or not pronto_code:
                _LOGGER.warning(
                    "Invalid button config in device '%s': name=%s, has_code=%s",
                    device_name, button_name, bool(pronto_code)
                )
                continue

            entities.append(
                CustomIRButton(
                    coordinator,
                    config_entry,
                    device_identifier,
                    device_name,
                    target,
                    button_name,
                    pronto_code,
                    via_device,
                )
            )

    if entities:
        _LOGGER.info("Created %d custom IR button entities", len(entities))
        async_add_entities(entities)
    else:
        _LOGGER.info("No custom IR buttons were created")


class CustomIRButton(ButtonEntity):
    """Custom IR button entity using Pronto codes."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator,
        config_entry,
        device_identifier,
        device_name,
        target,
        button_name,
        pronto_code,
        via_device=None,
    ):
        self.coordinator = coordinator
        self._config_entry = config_entry
        self._device_identifier = device_identifier
        self._device_name = device_name
        self._target = target
        self._button_name = button_name
        self._pronto_code = pronto_code
        self._via_device = via_device

        button_key = button_name.lower().replace(" ", "_").replace("/", "_")
        device_key = device_name.lower().replace(" ", "_").replace("/", "_")
        self._attr_unique_id = f"{config_entry.entry_id}_custom_{device_key}_{button_key}"
        self._attr_name = button_name

    @property
    def device_info(self):
        info = {
            "identifiers": {self._device_identifier},
            "name": self._device_name,
            "manufacturer": "MHUB Custom",
            "model": "Custom IR Device",
        }
        if self._via_device:
            info["via_device"] = self._via_device
        return info

    async def async_press(self) -> None:
        _LOGGER.debug(
            "Custom IR button pressed: %s - %s (target=%s)",
            self._device_name, self._button_name, self._target
        )
        try:
            await self.coordinator.hass.services.async_call(
                DOMAIN,
                "send_pronto_ir",
                {
                    "target": self._target,
                    "pronto_code": self._pronto_code,
                },
                blocking=True,
            )
        except Exception as e:
            _LOGGER.error(
                "Failed to send custom IR command for %s - %s: %s",
                self._device_name, self._button_name, e
            )