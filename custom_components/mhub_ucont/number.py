from homeassistant.components.number import NumberEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
import logging

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up MHUB volume number entities."""
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
                "Zone %s (%s) has no outputs defined, skipping volume control",
                zone_id, zone_label
            )
            continue
        
        # Extract the first output ID (lowercase as per API spec)
        output_id = outputs[0].get("output_id", "").lower()
        
        if not output_id:
            _LOGGER.warning(
                "Zone %s (%s) has invalid output_id, skipping volume control",
                zone_id, zone_label
            )
            continue

        entities.append(
            MhubVolumeNumber(
                coordinator,
                entry_id,
                zone_id,
                zone_label,
                output_id,
            )
        )

    async_add_entities(entities)


class MhubVolumeNumber(CoordinatorEntity, NumberEntity):
    """Representation of an MHUB volume control."""
    
    _attr_min_value = 0
    _attr_max_value = 100
    _attr_step = 1
    _attr_native_unit_of_measurement = "%"

    def __init__(self, coordinator, entry_id, zone_id, zone_label, output_id):
        super().__init__(coordinator)

        self._zone_id = zone_id
        self._output_id = output_id

        self._attr_name = f"{zone_label} Volume"
        self._attr_unique_id = f"{entry_id}_{zone_id}_volume"

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
    def native_value(self):
        """Return the current volume level."""
        zones_state = self.coordinator.data.get("zones_state", [])
        
        for zone in zones_state:
            if zone.get("zone_id") == self._zone_id:
                state = zone.get("state", [])
                if state and len(state) > 0:
                    volume = state[0].get("volume")
                    if volume is not None:
                        try:
                            return int(volume)
                        except (ValueError, TypeError):
                            _LOGGER.warning(
                                "Invalid volume value for zone %s: %s",
                                self._zone_id, volume
                            )
                            return 0
        return 0

    async def async_set_native_value(self, value):
        """Set the volume level.
        
        Uses output_id (not zone_id) as required by the API.
        Uses optimistic update for instant UI feedback.
        """
        try:
            # Update local state immediately for instant UI response
            self._attr_native_value = int(value)
            self.async_write_ha_state()
            
            # Send command to MHUB
            await self.coordinator.api.set_output_volume(
                self._output_id,
                int(value),
            )
            
            # Request refresh in background (don't wait)
            self.coordinator.async_request_refresh()
            
        except Exception as e:
            _LOGGER.error(
                "Failed to set volume for zone %s (output %s): %s",
                self._zone_id, self._output_id, str(e)
            )
            # Refresh to get actual state on error
            await self.coordinator.async_request_refresh()
            raise