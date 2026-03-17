"""Custom IR button entities for MHUB uControl."""
import logging
from pathlib import Path
import yaml

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
    
    # Look for custom_ir_devices.yaml in the integration folder
    integration_path = Path(__file__).parent
    config_file = integration_path / "custom_ir_devices.yaml"
    
    if not config_file.exists():
        _LOGGER.info("No custom_ir_devices.yaml found, skipping custom IR devices")
        return
    
    try:
        # Use async file read to avoid blocking
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
    
    # The entry title is what the user sees in HA (set during config flow)
    entry_title = config_entry.title
    
    entities = []
    
    for device in devices:
        device_name = device.get("name")
        target = device.get("target")
        buttons = device.get("buttons", [])
        mhub_filter = device.get("mhub")  # Optional - if set, only load on matching entry
        
        # Skip if this device is assigned to a different MHUB
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
        
        # Scope device identifier to entry so two MHUBs don't share devices
        device_key = device_name.lower().replace(" ", "_").replace("/", "_")
        device_identifier = (DOMAIN, f"{config_entry.entry_id}_custom_ir_{device_key}")
        
        _LOGGER.info(
            "Loading custom IR device '%s' with %d buttons (target=%s, mhub=%s)",
            device_name, len(buttons), target, entry_title
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
    ):
        """Initialize the custom IR button."""
        self.coordinator = coordinator
        self._config_entry = config_entry
        self._device_identifier = device_identifier
        self._device_name = device_name
        self._target = target
        self._button_name = button_name
        self._pronto_code = pronto_code
        
        # Create unique ID using config_entry
        button_key = button_name.lower().replace(" ", "_").replace("/", "_")
        device_key = device_name.lower().replace(" ", "_").replace("/", "_")
        self._attr_unique_id = f"{config_entry.entry_id}_custom_{device_key}_{button_key}"
        
        # Set entity name
        self._attr_name = button_name
        
        _LOGGER.debug(
            "Created custom IR button: device=%s, button=%s, target=%s",
            device_name, button_name, target
        )

    @property
    def device_info(self):
        """Return device info."""
        return {
            "identifiers": {self._device_identifier},
            "name": self._device_name,
            "manufacturer": "MHUB Custom",
            "model": "Custom IR Device",
            "via_device": (DOMAIN, self._config_entry.entry_id),
        }

    async def async_press(self) -> None:
        """Handle button press - send Pronto IR code."""
        _LOGGER.debug(
            "Custom IR button pressed: %s - %s (target=%s)",
            self._device_name, self._button_name, self._target
        )
        
        # Use the coordinator's send_pronto_ir service
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
            _LOGGER.info(
                "Sent custom IR command: %s - %s",
                self._device_name, self._button_name
            )
        except Exception as e:
            _LOGGER.error(
                "Failed to send custom IR command for %s - %s: %s",
                self._device_name, self._button_name, e
            )