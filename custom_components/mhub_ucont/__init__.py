"""MHUB uControl integration for Home Assistant."""
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import Platform
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.exceptions import ConfigEntryNotReady

from .api import MhubAPI
from .coordinator import MhubCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SWITCH,
    Platform.SENSOR,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up MHUB uControl from a config entry."""
    host = entry.data["host"]
    
    _LOGGER.info("Setting up MHUB uControl integration for host: %s", host)
    
    try:
        session = async_get_clientsession(hass)
        api = MhubAPI(host, session)

        coordinator = MhubCoordinator(hass, api)
        await coordinator.async_config_entry_first_refresh()

        hass.data.setdefault("mhub_ucont", {})
        hass.data["mhub_ucont"][entry.entry_id] = coordinator

        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        
        # Register Pronto IR service with automatic port mapping
        async def send_pronto_ir_service(call):
            """Service to send raw Pronto IR code with automatic port mapping."""
            
            # Get parameters
            target = call.data.get("target")  # e.g., "output_c", "input_2"
            port = call.data.get("port")  # Direct port number (optional)
            pronto_code = call.data.get("pronto_code")
            
            if not pronto_code:
                _LOGGER.error("Missing required parameter: pronto_code")
                return
            
            # Determine port number
            actual_port = None
            
            if port:
                # Direct port specified
                actual_port = port
                _LOGGER.info("Using direct port: %s", actual_port)
            elif target:
                # Map target to port
                actual_port = await _map_target_to_port(coordinator, target)
                if not actual_port:
                    _LOGGER.error("Could not map target '%s' to IR port", target)
                    return
                _LOGGER.info("Mapped target '%s' to port %s", target, actual_port)
            else:
                _LOGGER.error("Must specify either 'target' or 'port'")
                return
            
            # Send IR command
            _LOGGER.info("Sending Pronto IR to port %s", actual_port)
            result = await coordinator.api.send_pronto_ir(actual_port, pronto_code)
            
            if result:
                _LOGGER.debug("Pronto IR sent successfully: %s", result)
            else:
                _LOGGER.error("Failed to send Pronto IR")
        
        async def _map_target_to_port(coordinator, target):
            """Map target (output_a, input_2, etc.) to IR port number.
            
            Based on HDA API documentation page 12:
            - IR ports use sequential numbering starting from 1
            - Backwards (input) IR ports come first
            - Forwards (output) IR ports come after
            - Example 4x4: Inputs 1-4 (ports 1-4), Outputs A-D (ports 5-8)
            """
            target = target.lower().strip()
            
            # Get IR port configuration from API
            info = await coordinator.api.get_system_info()
            ir_config = info.get("data", {}).get("io_data", {}).get("ir", {})
            
            backwards = ir_config.get("backwards", {})
            forwards = ir_config.get("forwards", {})
            
            backwards_start = int(backwards.get("start_id", 1)) if backwards.get("start_id") else None
            backwards_ports = int(backwards.get("ports", 0)) if backwards.get("ports") else 0
            
            forwards_start = int(forwards.get("start_id", 1)) if forwards.get("start_id") else None
            forwards_ports = int(forwards.get("ports", 0)) if forwards.get("ports") else 0
            
            # Parse target
            if target.startswith("output_") or target.startswith("output "):
                # Output target (e.g., "output_d", "output d", "output_D")
                output_letter = target.replace("output_", "").replace("output ", "").strip().lower()
                
                if forwards_start is None:
                    _LOGGER.error("No forward IR ports configured")
                    return None
                
                # Find the port by checking the ir_port_to_output mapping from coordinator
                ir_port_to_output = coordinator.data.get("ir_port_to_output", {})
                
                # Search for this output letter in the mapping
                for port_id, mapped_output in ir_port_to_output.items():
                    if mapped_output == output_letter:
                        _LOGGER.debug("Output %s mapped to port %d via coordinator mapping", 
                                      output_letter.upper(), port_id)
                        return port_id
                
                # If not found in mapping, log error
                _LOGGER.error("Output %s not found in IR port mapping. Available outputs: %s", 
                              output_letter.upper(), list(ir_port_to_output.values()))
                return None
                        
            elif target.startswith("input_") or target.startswith("input "):
                # Input target (e.g., "input_2", "input 2")
                input_num = target.replace("input_", "").replace("input ", "").strip()
                
                if backwards_start is None:
                    _LOGGER.error("No backward IR ports configured")
                    return None
                
                try:
                    input_number = int(input_num)
                    if input_number < 1 or input_number > backwards_ports:
                        _LOGGER.error("Input %d exceeds available ports (%d)", input_number, backwards_ports)
                        return None
                    
                    # Calculate port: Input 1 = backwards_start + 0, Input 2 = +1, etc.
                    port = backwards_start + (input_number - 1)
                    _LOGGER.debug("Input %d: backwards_start(%d) + offset(%d) = port %d",
                                  input_number, backwards_start, input_number - 1, port)
                    return port
                except ValueError:
                    _LOGGER.error("Invalid input number: %s", input_num)
                    return None
            else:
                _LOGGER.error("Could not parse target: %s (use 'output_a' or 'input_1' format)", target)
                return None
        
        hass.services.async_register(
            "mhub_ucont",
            "send_pronto_ir",
            send_pronto_ir_service
        )
        
        _LOGGER.info("Successfully set up MHUB uControl integration")
        return True
        
    except Exception as err:
        _LOGGER.error("Error setting up MHUB uControl: %s", err)
        raise ConfigEntryNotReady from err


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info("Unloading MHUB uControl integration")
    
    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, PLATFORMS
    )
    
    if unload_ok:
        hass.data["mhub_ucont"].pop(entry.entry_id)
        _LOGGER.info("Successfully unloaded MHUB uControl integration")
    
    return unload_ok