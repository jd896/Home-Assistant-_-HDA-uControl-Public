from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
import logging

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up MHUB mute switch entities."""
    coordinator = hass.data["mhub_ucont"][entry.entry_id]
    entry_id = entry.entry_id

    entities = []

    zones = coordinator.data.get("zones", [])

    for zone in zones:
        zone_id = zone.get("zone_id")
        zone_label = zone.get("zone_label")
        outputs = zone.get("outputs", [])
        
        if not outputs:
            _LOGGER.warning(
                "Zone %s (%s) has no outputs defined, skipping mute control",
                zone_id, zone_label
            )
            continue
        
        # Extract the first output ID (lowercase as per API spec)
        output_id = outputs[0].get("output_id", "").lower()
        
        if not output_id:
            _LOGGER.warning(
                "Zone %s (%s) has invalid output_id, skipping mute control",
                zone_id, zone_label
            )
            continue

        entities.append(
            MhubMuteSwitch(
                coordinator,
                entry_id,
                zone_id,
                zone_label,
                output_id,
            )
        )

    async_add_entities(entities)


class MhubMuteSwitch(CoordinatorEntity, SwitchEntity):
    """Representation of an MHUB mute switch."""

    def __init__(self, coordinator, entry_id, zone_id, zone_label, output_id):
        super().__init__(coordinator)

        self._zone_id = zone_id
        self._output_id = output_id

        self._attr_name = "Mute"
        self._attr_unique_id = f"{entry_id}_{zone_id}_mute"

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

    @property
    def is_on(self):
        """Return True if mute is enabled."""
        zones_state = self.coordinator.data.get("zones_state", [])
        
        for zone in zones_state:
            if zone.get("zone_id") == self._zone_id:
                state = zone.get("state", [])
                if state and len(state) > 0:
                    mute = state[0].get("mute")
                    if mute is not None:
                        return bool(mute)
        return False

    async def async_turn_on(self):
        """Enable mute.
        
        Uses output_id (not zone_id) as required by the API.
        Uses optimistic update for instant UI feedback.
        """
        try:
            # Update local state immediately for instant UI response
            self._attr_is_on = True
            self.async_write_ha_state()
            
            # Send command to MHUB
            await self.coordinator.api.set_output_mute(
                self._output_id,
                True
            )
            
            # Request refresh in background (don't wait)
            self.coordinator.async_request_refresh()
            
        except Exception as e:
            _LOGGER.error(
                "Failed to enable mute for zone %s (output %s): %s",
                self._zone_id, self._output_id, str(e)
            )
            # Refresh to get actual state on error
            await self.coordinator.async_request_refresh()
            raise

    async def async_turn_off(self):
        """Disable mute.
        
        Uses output_id (not zone_id) as required by the API.
        Uses optimistic update for instant UI feedback.
        """
        try:
            # Update local state immediately for instant UI response
            self._attr_is_on = False
            self.async_write_ha_state()
            
            # Send command to MHUB
            await self.coordinator.api.set_output_mute(
                self._output_id,
                False
            )
            
            # Request refresh in background (don't wait)
            self.coordinator.async_request_refresh()
            
        except Exception as e:
            _LOGGER.error(
                "Failed to disable mute for zone %s (output %s): %s",
                self._zone_id, self._output_id, str(e)
            )
            # Refresh to get actual state on error
            await self.coordinator.async_request_refresh()
            raise