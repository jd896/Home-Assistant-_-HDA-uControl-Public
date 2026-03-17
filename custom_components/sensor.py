from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
import logging

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up MHUB sensor entities."""
    coordinator = hass.data["mhub_ucont"][entry.entry_id]
    entry_id = entry.entry_id

    entities = []

    zones = coordinator.data.get("zones", [])

    for zone in zones:
        zone_id = zone.get("zone_id")
        zone_label = zone.get("zone_label")
        
        if not zone_id or not zone_label:
            _LOGGER.warning("Zone has missing id or label: %s", zone)
            continue
        
        entities.append(
            MhubActiveSourceSensor(
                coordinator,
                entry_id,
                zone_id,
                zone_label,
            )
        )

    async_add_entities(entities)


class MhubActiveSourceSensor(CoordinatorEntity, SensorEntity):
    """Representation of an MHUB active source sensor."""

    def __init__(self, coordinator, entry_id, zone_id, zone_label):
        super().__init__(coordinator)

        self._zone_id = zone_id

        self._attr_name = "Active Source"
        self._attr_unique_id = f"{entry_id}_{zone_id}_active_source"
        self._attr_icon = "mdi:video-input-hdmi"

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
        """Return the label of the currently active source."""
        zones_state = self.coordinator.data.get("zones_state", [])
        sources = self.coordinator.data.get("sources", {})

        for zone in zones_state:
            if zone.get("zone_id") == self._zone_id:
                state = zone.get("state", [])
                if state and len(state) > 0:
                    input_id = state[0].get("input_id")
                    if input_id is not None:
                        # Convert to string for key lookup
                        input_id_str = str(input_id)
                        source_label = sources.get(input_id_str)
                        
                        if source_label:
                            return source_label
                        else:
                            _LOGGER.debug(
                                "Input ID %s not found in sources for zone %s",
                                input_id_str, self._zone_id
                            )
                            return f"Input {input_id}"

        return None

    @property
    def extra_state_attributes(self):
        """Return additional state attributes."""
        zones_state = self.coordinator.data.get("zones_state", [])
        
        for zone in zones_state:
            if zone.get("zone_id") == self._zone_id:
                state = zone.get("state", [])
                if state and len(state) > 0:
                    zone_state = state[0]
                    return {
                        "input_id": zone_state.get("input_id"),
                        "output_id": zone_state.get("output_id"),
                        "audio_id": zone_state.get("audio_id"),
                        "arc": zone_state.get("arc"),
                    }
        
        return {}